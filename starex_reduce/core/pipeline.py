"""The five reduction stages: names, texts shown in the UI and the files they read/write."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageDef:
    id: str
    number: int
    title: str
    subtitle: str  # grey line in the ribbon
    description: str  # step header text
    suffix: str  # output file suffix: Target_<suffix>.fit
    input_suffix: str | None  # None = raw lights


STAGES: tuple[StageDef, ...] = (
    StageDef(
        "files", 1, "Files", "Masters, stack lights",
        "Pick the target lights and the reference star lights, then stack them. "
        "The calibration masters they need are managed in the library at the bottom.",
        "S", None,
    ),
    StageDef(
        "geometry", 2, "Geometry", "Flip, tilt, hot pixels",
        "Works on the stacked images. Make the spectrum horizontal and straight: let the tool "
        "detect the tilt, or draw a line along the spectrum yourself.",
        "G", "S",
    ),
    StageDef(
        "extract", 3, "1D Extract", "Bands, 1D profile",
        "Turn each stacked 2D image into a 1D profile. The blue band is the star, the two brown "
        "bands measure the sky that gets subtracted.",
        "X", "G",
    ),
    StageDef(
        "wavelength", 4, "Wavelength", "Balmer auto-fit, R value",
        "Match the Balmer lines of the reference star, fit the dispersion, and carry it to the "
        "target.",
        "W", "X",
    ),
    StageDef(
        "response", 5, "Response", "Response, normalise, crop",
        "Divide your reference star by its library spectrum to get the instrument and atmosphere "
        "response. Apply it to the target, then normalise and crop.",
        "R", "W",
    ),
)
STAGE_IDS: tuple[str, ...] = tuple(s.id for s in STAGES)
IMAGES: tuple[str, ...] = ("Target", "Reference")


def stage_def(stage_id: str) -> StageDef:
    return STAGES[STAGE_IDS.index(stage_id)]


def output_name(stage_id: str, image: str) -> str:
    return f"{image}_{stage_def(stage_id).suffix}.fit"


def input_name(stage_id: str, image: str) -> str | None:
    suffix = stage_def(stage_id).input_suffix
    return f"{image}_{suffix}.fit" if suffix else None


@dataclass
class StageRun:
    """What a stage job returns to the UI thread."""

    stage: str
    outputs: dict[str, str]  # image -> file name in the session folder
    summaries: dict[str, str]  # image -> one-line result
    log: list[str]
    data: dict | None = None  # extra results for the screen (not persisted)
    ribbon: str = ""  # short status for the ribbon (defaults to the joined summaries)

    @property
    def summary(self) -> str:
        return " · ".join(self.summaries.values())
