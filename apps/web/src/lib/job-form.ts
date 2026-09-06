import { z } from "zod";

import type {
  AnswerType,
  DraftQuestion,
  JobCreate,
  JobDraft,
  QuestionInput,
} from "@/lib/types";

/**
 * Form shape and validation for creating a role.
 *
 * The form deliberately holds two fields the API does not have:
 * `enum_options_raw` and `rule_raw`. Both are free text a recruiter types,
 * and both are parsed into structured values on submit. Asking someone to
 * fill in an operator dropdown and a value box to express "at least two
 * years" would be a worse experience than letting them type it.
 */

export const ANSWER_TYPES: { value: AnswerType; label: string }[] = [
  { value: "STRING", label: "Text" },
  { value: "NUMBER", label: "Number" },
  { value: "BOOLEAN", label: "Yes or no" },
  { value: "ENUM", label: "One of a list" },
];

export const LANGUAGES = [
  "ENGLISH",
  "HINDI",
  "TAMIL",
  "TELUGU",
  "KANNADA",
  "MARATHI",
  "MALAYALAM",
  "GUJARATI",
  "BENGALI",
] as const;

/** Voices the provider offers. The names hint at, but do not promise, a locale. */
export const VOICES = ["NEHA", "ROY", "ZOE", "SAM", "MIRA", "EESHA"] as const;

export const RULE_HINT: Record<string, string> = {
  NUMBER: "at least 2",
  BOOLEAN: "yes",
  STRING: "contains python",
  ENUM: "Night shift, Either",
};

const questionSchema = z.object({
  label: z.string().trim().min(1, "Give the column a heading").max(120),
  text: z
    .string()
    .trim()
    .min(3, "Write the question the agent should ask")
    .max(500),
  answer_type: z.enum(["STRING", "NUMBER", "BOOLEAN", "ENUM"]),
  enum_options_raw: z.string().optional(),
  rule_raw: z.string().optional(),
  weight: z.coerce.number().min(0).max(10),
  is_knockout: z.boolean(),
});

export const jobFormSchema = z.object({
  title: z.string().trim().min(2, "Give the role a title").max(200),
  company_name: z.string().trim().min(1, "Who is hiring?").max(200),
  location: z.string().trim().max(200).optional(),
  description_raw: z.string().max(20_000).optional(),
  language: z.string(),
  voice_persona: z.string(),
  questions: z
    .array(questionSchema)
    .min(1, "Add at least one question")
    .max(15)
    .superRefine((questions, ctx) => {
      // Two questions sharing a heading would collapse into one column,
      // silently losing an answer, so it is caught before submit.
      const seen = new Map<string, number>();
      questions.forEach((question, index) => {
        const key = question.label.trim().toLowerCase();
        if (!key) return;
        if (seen.has(key)) {
          ctx.addIssue({
            code: "custom",
            path: [index, "label"],
            message: "Another question already uses this heading",
          });
        }
        seen.set(key, index);
      });
    }),
});

export type JobFormValues = z.infer<typeof jobFormSchema>;

export function emptyQuestion(): JobFormValues["questions"][number] {
  return {
    label: "",
    text: "",
    answer_type: "STRING",
    enum_options_raw: "",
    rule_raw: "",
    weight: 1,
    is_knockout: false,
  };
}

export function defaultJobValues(): JobFormValues {
  return {
    title: "",
    company_name: "",
    location: "",
    description_raw: "",
    language: "ENGLISH",
    voice_persona: "NEHA",
    questions: [emptyQuestion()],
  };
}

function splitList(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

/**
 * Turn typed shorthand into a structured scoring rule.
 *
 * Returns `null` when nothing usable was written, which the backend
 * treats as "any answer counts". That is the right default: a question
 * worth asking is not necessarily a question worth ranking on.
 */
export function parseRule(
  raw: string | undefined,
  answerType: AnswerType,
  options: string[],
): Record<string, unknown> | null {
  const text = (raw ?? "").trim().toLowerCase();
  if (!text) return null;

  if (answerType === "BOOLEAN") {
    if (["yes", "true", "y"].includes(text)) return { op: "is_true" };
    if (["no", "false", "n"].includes(text)) return { op: "is_false" };
    return null;
  }

  if (answerType === "NUMBER") {
    const number = Number.parseFloat(text.replace(/[^\d.-]/g, ""));
    if (Number.isNaN(number)) return null;
    // "under 30 days" and "at most 30" both mean an upper bound.
    if (/(<=|under|below|less|at most|no more|within|max)/.test(text)) {
      return { op: "lte", value: number };
    }
    return { op: "gte", value: number };
  }

  if (answerType === "ENUM") {
    const wanted = splitList(raw).filter((value) =>
      options.length === 0
        ? true
        : options.some(
            (option) => option.toLowerCase() === value.toLowerCase(),
          ),
    );
    return wanted.length > 0 ? { op: "in", value: wanted } : null;
  }

  const contains = text.replace(/^contains\s+/, "").trim();
  return contains ? { op: "contains", value: contains } : null;
}

/** Convert the form's values into the API's create payload. */
export function toJobCreate(values: JobFormValues): JobCreate {
  const questions: QuestionInput[] = values.questions.map((question) => {
    const options =
      question.answer_type === "ENUM"
        ? splitList(question.enum_options_raw)
        : [];
    return {
      label: question.label.trim(),
      text: question.text.trim(),
      answer_type: question.answer_type,
      enum_options: options.length > 0 ? options : null,
      extraction_hint: null,
      weight: question.weight,
      is_knockout: question.is_knockout,
      scoring_rule: parseRule(question.rule_raw, question.answer_type, options),
    };
  });

  return {
    title: values.title.trim(),
    company_name: values.company_name.trim(),
    location: values.location?.trim() || null,
    description_raw: values.description_raw ?? "",
    language: values.language,
    voice_persona: values.voice_persona,
    persona_name: null,
    timezone: "Asia/Kolkata",
    questions,
  };
}

/**
 * Turn an extracted question into the form's scoring shorthand.
 *
 * Only fills in what the description actually stated. A yes-or-no
 * question that carries weight obviously scores on "yes", and a numeric
 * threshold the description gave becomes a minimum. Everything else is
 * left blank, because how candidates are ranked is a judgement about
 * this hire rather than something to infer from prose.
 */
function deriveRule(question: DraftQuestion): string {
  if (
    question.answer_type === "BOOLEAN" &&
    (question.is_knockout || question.weight > 0)
  ) {
    return "yes";
  }
  if (
    question.answer_type === "NUMBER" &&
    typeof question.minimum === "number"
  ) {
    return `at least ${question.minimum}`;
  }
  return "";
}

/**
 * Pour an extracted draft into the form.
 *
 * Every value stays editable. The draft is a starting point that saves
 * retyping what the job description already said, not a decision.
 */
export function fromDraft(draft: JobDraft, jdText: string): JobFormValues {
  const questions = (draft.questions ?? []).map((question) => ({
    label: question.label,
    text: question.text,
    answer_type: question.answer_type,
    enum_options_raw: (question.enum_options ?? []).join(", "),
    rule_raw: deriveRule(question),
    weight: question.weight,
    is_knockout: question.is_knockout,
  }));

  return {
    title: draft.title ?? "",
    company_name: draft.company_name ?? "",
    location: draft.location ?? "",
    // The pasted text is kept as the description: the agent uses it to
    // answer questions the candidate asks about the role, so discarding
    // it after extraction would throw away the useful half.
    description_raw: jdText.trim(),
    language: draft.language ?? "ENGLISH",
    voice_persona: draft.voice_persona ?? "NEHA",
    questions: questions.length > 0 ? questions : [emptyQuestion()],
  };
}

/** A realistic starting point, so the form can be tried without typing. */
export function sampleJobValues(): JobFormValues {
  return {
    title: "Delivery Executive",
    company_name: "Acme Logistics",
    location: "Bengaluru",
    description_raw:
      "We are hiring delivery executives across Bengaluru for same-day parcel delivery. " +
      "You will need your own two-wheeler and a valid driving licence. Shifts are 9 hours " +
      "with one weekly off. Earnings are 18,000 to 24,000 per month including incentives, " +
      "with fuel reimbursed.",
    language: "HINDI",
    voice_persona: "NEHA",
    questions: [
      {
        label: "Years of experience",
        text: "How many years of delivery experience do you have?",
        answer_type: "NUMBER",
        enum_options_raw: "",
        rule_raw: "at least 1",
        weight: 2,
        is_knockout: false,
      },
      {
        label: "Owns two-wheeler",
        text: "Do you have your own two-wheeler and a valid driving licence?",
        answer_type: "BOOLEAN",
        enum_options_raw: "",
        rule_raw: "yes",
        weight: 3,
        is_knockout: true,
      },
      {
        label: "Preferred shift",
        text: "Would you prefer the day shift or the night shift?",
        answer_type: "ENUM",
        enum_options_raw: "Day shift, Night shift, Either",
        rule_raw: "",
        weight: 1,
        is_knockout: false,
      },
      {
        label: "Notice period",
        text: "How soon are you able to start?",
        answer_type: "STRING",
        enum_options_raw: "",
        rule_raw: "",
        weight: 1,
        is_knockout: false,
      },
    ],
  };
}
