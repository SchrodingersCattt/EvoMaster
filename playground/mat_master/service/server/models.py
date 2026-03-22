"""Pydantic request models for the MatMaster web API."""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    prompt: str
    workspace: str = './workspace'


class RenameRequest(BaseModel):
    path: str
    new_name: str
