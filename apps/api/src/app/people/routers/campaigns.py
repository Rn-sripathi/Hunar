"""Outreach campaign routes.

Reading a campaign reconciles it first. Hunar's status webhook fires only
once a call has finished, so ringing and in-progress transitions exist
only because a read goes and asks. Doing it on read rather than in a
background worker means the state is fresh exactly when someone is
looking at it, needs no scheduler, and cannot drift out of step with the
screen.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.deps import DbSession, SettingsDep, VoiceClient
from app.people.schemas import (
    CampaignCreate,
    CampaignDetail,
    CampaignSummary,
    LaunchOutreachReport,
)
from app.people.services import campaign_service

router = APIRouter(prefix="/campaigns")


@router.get("", response_model=list[CampaignSummary])
async def list_campaigns(session: DbSession) -> list[CampaignSummary]:
    return await campaign_service.list_campaigns(session)


@router.post("", response_model=CampaignDetail, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate, session: DbSession, settings: SettingsDep
) -> CampaignDetail:
    """Assemble a campaign, running the consent gate over every prospect.

    Creating a campaign places no calls. Prospects the gate refuses are
    reported in the detail's blocked count rather than stored, because a
    target row cannot exist without a consented number to point at.
    """
    campaign, _blocked = await campaign_service.create_campaign(session, settings, payload)
    return await campaign_service.campaign_detail(session, campaign)


@router.get("/{campaign_id}", response_model=CampaignDetail)
async def get_campaign(
    campaign_id: uuid.UUID, session: DbSession, client: VoiceClient
) -> CampaignDetail:
    """A campaign with its live call state and extracted answers."""
    campaign = await campaign_service.get_campaign(session, campaign_id)
    await campaign_service.reconcile_campaign(session, client, campaign)
    return await campaign_service.campaign_detail(session, campaign)


@router.post("/{campaign_id}/launch", response_model=LaunchOutreachReport)
async def launch(
    campaign_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    client: VoiceClient,
) -> LaunchOutreachReport:
    """Place the calls.

    Every target is re-checked against the consent gate here, not just
    when the campaign was built, because the clock moves between the two.
    """
    campaign = await campaign_service.get_campaign(session, campaign_id)
    return await campaign_service.launch_campaign(session, client, settings, campaign)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: uuid.UUID, session: DbSession) -> None:
    campaign = await campaign_service.get_campaign(session, campaign_id)
    await campaign_service.delete_campaign(session, campaign)
