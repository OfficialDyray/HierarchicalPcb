import logging
import math
from itertools import zip_longest
from typing import Callable, Dict, List, Optional, Tuple

import pcbnew

logger = logging.getLogger("hierpcb")


class ErrorLevel:
    INFO = 0
    WARNING = 1
    ERROR = 2


class ReportedError:
    def __init__(
        self,
        title: str,
        message: Optional[str] = None,
        level: ErrorLevel = ErrorLevel.ERROR,
        footprint: pcbnew.FOOTPRINT = None,
    ):
        self.title = title
        self.message = message
        self.level = level
        self.footprint = footprint

        logger.debug(str(self))

    def __str__(self):
        msg = [f"ERR.{self.level}\t{self.title}"]
        if self.message:
            msg += [f" Message: {self.message}"]
        if self.footprint:
            msg += [f" Footprint: {self.footprint.GetReference()}"]
        if self.sheet:
            msg += [f" Sheet: {self.sheet.identifier}"]
        if self.pcb:
            msg += [f" SubPCB: {self.pcb.path}"]

        return "\n".join(msg)


class PositionTransform:
    def __init__(self, template: pcbnew.FOOTPRINT, mutate: pcbnew.FOOTPRINT) -> None:
        # These are stored such that adding these to the position and rotation of the `template`
        # will yield the position and rotation of the `mutate`.
        self.anchor_template = template
        self.anchor_mutate = mutate

    def translate(self, pos_template: pcbnew.VECTOR2I) -> pcbnew.VECTOR2I:
        # Find the position of fp_template relative to the anchor_template:
        delta_x: int = pos_template.x - self.anchor_template.GetPosition().x
        delta_y: int = pos_template.y - self.anchor_template.GetPosition().y
        rotation = math.radians(
            self.anchor_mutate.GetOrientationDegrees()
            - self.anchor_template.GetOrientationDegrees()
        )

        # With this information, we can compute the net position after any rotation:
        new_x = (
            delta_y * math.sin(rotation)
            + delta_x * math.cos(rotation)
            + self.anchor_mutate.GetPosition().x
        )
        new_y = (
            delta_y * math.cos(rotation)
            - delta_x * math.sin(rotation)
            + self.anchor_mutate.GetPosition().y
        )
        return pcbnew.VECTOR2I(int(new_x), int(new_y))

    def orient(self, rot_template: float):
        return (
            rot_template
            - self.anchor_template.GetOrientation()
            + self.anchor_mutate.GetOrientation()
        )

class GroupManager:
    def __init__(self, board: pcbnew.BOARD, groupName: str) -> None:
        self.board: pcbnew.BOARD = board
        self.group = self._create_or_get(groupName)


    def _create_or_get(self, group_name: str) -> pcbnew.PCB_GROUP:
        """Get a group by name, creating it if it doesn't exist."""
        retGroup = None
        for group in self.board.Groups():
            if group.GetName() == group_name:
                retGroup = group
        if retGroup is None:
            retGroup = pcbnew.PCB_GROUP(None)
            retGroup.SetName(group_name)
            self.board.Add(retGroup)
        return retGroup

    def move(self, item: pcbnew.BOARD_ITEM) -> bool:
        """Force an item to be in our group, returning True if the item was moved."""
        moved = False
        # First, check if the footprint is already in the group:
        parent_group = item.GetParentGroup()
        # If the footprint is not already in the group, remove it from the current group:
        if parent_group and parent_group.GetName() != self.group.GetName():
            moved = True
            parent_group.RemoveItem(item)
            parent_group = None
        # If the footprint is not in any group, or was in the wrong group, add it to the right one:
        if parent_group is None:
            self.group.AddItem(item)

        return moved

## Contains information to:
#  MoveFootprints relative to anchor
#  Place footprints in the group

class ReplicateContext(PositionTransform, GroupManager):
    def __init__(
        self,
        sourceAnchorFootprint: pcbnew.FOOTPRINT, 
        targetAnchorFootprint: pcbnew.FOOTPRINT,
        groupName
        ):

        self._sourceBoard = sourceAnchorFootprint.GetBoard()
        self._targetBoard = targetAnchorFootprint.GetBoard()

        PositionTransform.__init__(self, sourceAnchorFootprint, targetAnchorFootprint)

        GroupManager.__init__(self, self._sourceBoard, groupName)

    @property
    def sourceBoard(self):
        return self._sourceBoard

    @property
    def targetBoard(self):
        return self._targetBoard


def clear_volatile_items(group: pcbnew.PCB_GROUP):
    """Remove all Traces, Drawings, Zones in a group."""
    board = group.GetBoard()

    itemTypesToRemove = (
        # Traces
        pcbnew.PCB_TRACK, pcbnew.ZONE,
        # Drawings
        pcbnew.PCB_SHAPE, pcbnew.PCB_TEXT,
        # Zones
        pcbnew.ZONE
    )

    for item in group.GetItems():

        # Gets all drawings in a group
        if isinstance(item.Cast(), itemTypesToRemove):
            # Remove every drawing
            board.RemoveNative(item)


def copy_drawings(context: ReplicateContext):
    for sourceDrawing in context.sourceBoard.GetDrawings(): 
        
        newDrawing = sourceDrawing.Duplicate()
        context.targetBoard.Add(newDrawing)

        # Set New Position
        newDrawing.SetPosition(context.translate(sourceDrawing.GetPosition()))

        # Drawings dont have .SetOrientation()
        # instead do a relative rotation
        newDrawing.Rotate(newDrawing.GetPosition(), context.orient(pcbnew.ANGLE_0))

        context.move(newDrawing)


def copy_traces(context: ReplicateContext, netMapping: dict):
    for sourceTrack in context.sourceBoard.Tracks():
        # Copy track to trk:
        # logger.info(f"{track} {type(track)} {track.GetStart()} -> {track.GetEnd()}")
        
        newTrack = sourceTrack.Duplicate()
        context.targetBoard.Add(newTrack)

        sourceNetCode = sourceTrack.GetNetCode()
        newNetCode = netMapping.get(sourceNetCode, 0)
        newTrack.SetNet(context.targetBoard.FindNet(newNetCode))

        # Sets Track start and end point
        # Via's ignore the end point, just copying anyways
        newTrack.SetStart(context.translate(sourceTrack.GetStart()))
        newTrack.SetEnd  (context.translate(sourceTrack.GetEnd()  ))

        if type(newTrack) == pcbnew.PCB_VIA:
            newTrack.SetIsFree(False)

        context.move(newTrack)


def copy_zones(context: ReplicateContext, netMapping: dict):

    transform   = self._transformer
    groupMan    = self._groupMan

    footprintNetMapping = self._footprintNetMapping

    for sourceZone in context.sourceBoard.Zones():
        
        newZone = sourceZone.Duplicate()

        sourceNetCode = sourceZone.GetNetCode()
        newNetCode = footprintNetMapping.get(sourceNetCode, 0)
        newZone.SetNet(context.targetBoard.FindNet(newNetCode))

        context.targetBoard.Add(newZone)

        # Set New Position
        # newZone.SetPosition(transform.translate(zone.GetPosition()))

        # Temporary Workaround:
        # Move zone to 0,0 by moving relative
        newZone.Move(-newZone.GetPosition())
        # Move zone to correct location
        newZone.Move(context.translate(sourceZone.GetPosition()))

        # Drawings dont have .SetOrientation()
        # instead do a relative rotation
        newZone.Rotate(newZone.GetPosition(), context.orient(pcbnew.ANGLE_0))

        context.move(newZone)


def copy_footprint_fields(
    sourceFootprint: pcbnew.FOOTPRINT,
    targetFootprint: pcbnew.FOOTPRINT,
):
    transform = PositionTransform(sourceFootprint, targetFootprint)

    # NOTE: Non-center aligned Fields position changes with rotation.
    #       This is not a bug. The replicated pcbs are behaving the 
    #       exact same as the original would when rotated.

    # Do any other field values need preserved?
    originalReference = targetFootprint.GetReference()

    # Remove Existing footprint fields
    for existingField in targetFootprint.GetFields():
        targetFootprint.RemoveNative(existingField)
    
    # Add all the source fields and move them
    for sourceField in sourceFootprint.GetFields():
        newField = sourceField.CloneField()
        newField.SetParent(targetFootprint)
        
        newField.SetPosition(transform.translate(sourceField.GetPosition()))
        newField.Rotate(newField.GetPosition(), transform.orient(pcbnew.ANGLE_0))

        targetFootprint.AddField(newField)

    targetFootprint.SetReference(originalReference)


def copy_footprint_data(
    sourceFootprint: pcbnew.FOOTPRINT,
    targetFootprint: pcbnew.FOOTPRINT,
):
    transform = PositionTransform(sourceFootprint, targetFootprint)

    copy_footprint_fields(sourceFootprint, targetFootprint)

    # Most definetly exists a better way to do this...
    # Maybe footprint cloning? 
    if sourceFootprint.IsFlipped() != targetFootprint.IsFlipped():
        targetFootprint.Flip(targetFootprint.GetPosition(), False)

    # The list of properties is from the ReplicateLayout plugin. Thanks @MitjaNemec!
    targetFootprint.SetLocalClearance(sourceFootprint.GetLocalClearance())
    targetFootprint.SetLocalSolderMaskMargin(sourceFootprint.GetLocalSolderMaskMargin())
    targetFootprint.SetLocalSolderPasteMargin(sourceFootprint.GetLocalSolderPasteMargin())
    targetFootprint.SetLocalSolderPasteMarginRatio(
        sourceFootprint.GetLocalSolderPasteMarginRatio()
    )
    targetFootprint.SetZoneConnection(sourceFootprint.GetZoneConnection())

    # Move the footprint:
    targetFootprint.SetPosition(transform.translate(sourceFootprint.GetPosition()))
    targetFootprint.SetOrientation(transform.orient(sourceFootprint.GetOrientation()))


def enforce_position_footprints(
    context: ReplicateContext,
    fpTranslator
) -> dict:

    fpTranslator = self._fpTranslator

    # The keys are the sub-pcb net codes
    # The values are the new net codes
    footprintNetMapping = {}

    # For each footprint in the sub-PCB, find the corresponding footprint on the board:
    for sourceFootprint in context.sourceBoard.GetFootprints():
        # Find the corresponding footprint on the board:

        targetFootprint = fpTranslator(sourceFootprint)

        if not targetFootprint:
            continue

        # Copy the properties and move the template to the target:
        copy_footprint_data(sourceFootprint, targetFootprint)

        # Assumes pads are ordered by the pad number
        for sourcePadNum, sourcePad in enumerate(sourceFootprint.Pads()):
            targetPad = targetFootprint.Pads()[sourcePadNum]

            sourceCode = sourcePad.GetNetCode()
            targetCode = targetPad.GetNetCode()
            footprintNetMapping[sourceCode] = targetCode

        # Move the footprint into the group if one is provided:
        context.move(targetFootprint)
    
    return footprintNetMapping