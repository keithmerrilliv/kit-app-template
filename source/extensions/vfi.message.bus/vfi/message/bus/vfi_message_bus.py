import omni.usd
import carb
import json
from omni.kit.viewport.utility import get_active_viewport_window
from pxr import Gf, UsdGeom, Usd

from vfi.interaction.util import (
    apply_transformation_to_prim,
    get_prim_full_transform,
    get_scene_up_axis,
    send_message_to_client,
    convert_client_delta_to_stage,
)


BAY_SCOPE_PATH = "/World/Cameras/bays/"

# Sensitivity constants for gesture mapping
ORBIT_SENSITIVITY = 150.0  # degrees per meter of drag
ZOOM_SENSITIVITY = 1.0     # 1:1 mapping of meters to camera dolly


def parse_message(event):
    carb.log_info("Messaged recieved")
    payload = event.payload
    message = json.loads(payload['message'])
    return message


def set_bay_camera(event):
    message = parse_message(event)
    bay_number = message.get('bay')
    carb.log_info(f"Bay number: {BAY_SCOPE_PATH + bay_number}")
    viewport_window = get_active_viewport_window()
    if not viewport_window:
        carb.log_info("viewport doesnt exist")
        return
    viewport_window.viewport_api.camera_path = BAY_SCOPE_PATH + bay_number


def set_animation(event):
    message = parse_message(event)
    anim_action = message.get('animationAction')
    carb.log_info(anim_action)
    timeline = omni.timeline.get_timeline_interface()
    if anim_action == 'Play':
        timeline.play()
    elif anim_action == "Pause":
        if timeline.is_playing():
            timeline.pause()
    elif anim_action == "Stop":
        timeline.stop()
    elif anim_action == "Rewind":
        timeline.stop()
        timeline.play()


def send_prim_data(event):
    message = parse_message(event)
    prim_data = message.get('PrimPath')
    attr_name = "in_transit"
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_data)
    if prim:
        omni.usd.get_context().get_selection().set_selected_prim_paths([prim_data], True)
        attr = prim.GetAttribute(attr_name)
        if attr.Get() is None:
            attr_name = "in_online"
            attr = prim.GetAttribute(attr_name)
        message = {"Type": "PrimTap", "PrimPath": prim_data, "MetadataName": str(attr_name), "MetadataValue": str(attr.Get())}
        send_message(json.dumps(message))
    else:
        carb.log_info("Prim not found")


def send_message(return_message):
    omni.kit.app.queue_event("omni.kit.cloudxr.send_message", payload={"message": return_message})


# ---------------------------------------------------------------------------
# Drag & Zoom gesture handlers
# ---------------------------------------------------------------------------

def _get_camera_prim():
    """Get the active viewport camera prim and stage."""
    viewport_window = get_active_viewport_window()
    if not viewport_window:
        return None, None, None
    viewport_api = viewport_window.viewport_api
    camera_path = viewport_api.get_active_camera()
    if not camera_path:
        return None, None, None
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return None, None, None
    prim = stage.GetPrimAtPath(camera_path)
    if not prim:
        return None, None, None
    return prim, camera_path, stage


def _apply_drag_to_camera(dx, dy, dz):
    """Orbit the camera around the scene origin based on client-space deltas."""
    prim, camera_path, stage = _get_camera_prim()
    if not prim:
        return

    xformable = UsdGeom.Xformable(prim)
    current_matrix = xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    current_matrix = Gf.Matrix4d(current_matrix)

    # Determine the up axis in stage space
    is_z_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    up_vec = Gf.Vec3d(0, 0, 1) if is_z_up else Gf.Vec3d(0, 1, 0)

    # Extract camera's local right vector from the transform (first row of 3x3)
    right_vec = Gf.Vec3d(current_matrix[0][0], current_matrix[0][1], current_matrix[0][2]).GetNormalized()

    # Orbit angles from client deltas (in meters -> degrees)
    yaw = -dx * ORBIT_SENSITIVITY
    pitch = -dy * ORBIT_SENSITIVITY

    # Orbit pivot is the scene origin
    pivot = Gf.Vec3d(0, 0, 0)

    # Build rotation quaternions
    yaw_rotation = Gf.Rotation(up_vec, yaw)
    pitch_rotation = Gf.Rotation(right_vec, pitch)
    combined_rotation = pitch_rotation * yaw_rotation

    # Rotate camera position around pivot
    position = current_matrix.ExtractTranslation()
    offset = position - pivot
    rotated_offset = combined_rotation.TransformDir(offset)
    new_position = pivot + rotated_offset

    # Rotate camera orientation
    rot_matrix = Gf.Matrix4d(1.0)
    rot_matrix.SetRotate(combined_rotation)
    new_matrix = current_matrix * rot_matrix

    # Apply new position while keeping the rotated orientation
    new_matrix.SetTranslateOnly(new_position)

    apply_transformation_to_prim(camera_path, new_matrix)


def _apply_drag_to_prim(prim_path, dx, dy, dz):
    """Translate a prim by the client-space delta."""
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        carb.log_info(f"Drag target prim not found: {prim_path}")
        return

    # Convert client delta (Y-up, meters) to stage coords
    delta = convert_client_delta_to_stage(dx, dy, dz, stage)

    # Read the current TypeTransform xform op value (or identity if none exists)
    xformable = UsdGeom.Xformable(prim)
    current_matrix = Gf.Matrix4d(1.0)
    for op in xformable.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTransform:
            val = op.Get()
            if val:
                current_matrix = Gf.Matrix4d(val)
            break

    # Add delta to translation, preserving rotation/scale
    translation = current_matrix.ExtractTranslation()
    translation += delta
    current_matrix.SetTranslateOnly(translation)

    apply_transformation_to_prim(prim_path, current_matrix)

    # Send updated transform back to client
    client_transform = get_prim_full_transform(prim_path)
    if client_transform:
        send_message_to_client({
            "Type": "prim_transform",
            "PrimPath": prim_path,
            "Transform": client_transform,
        })


def handle_drag(event):
    """Handle drag gestures: orbit camera or translate selected prim."""
    message = parse_message(event)
    dx = float(message.get('deltaX', '0.0'))
    dy = float(message.get('deltaY', '0.0'))
    dz = float(message.get('deltaZ', '0.0'))
    phase = message.get('phase', 'changed')

    if phase != 'changed':
        return

    # Check if a prim is selected — if so, drag translates it
    selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
    if selected:
        carb.log_info(f"Drag prim: {selected[0]} delta=({dx}, {dy}, {dz})")
        _apply_drag_to_prim(selected[0], dx, dy, dz)
    else:
        carb.log_info(f"Drag camera delta=({dx}, {dy}, {dz})")
        _apply_drag_to_camera(dx, dy, dz)


def _apply_zoom_to_camera(delta):
    """Dolly the camera along its forward axis."""
    prim, camera_path, stage = _get_camera_prim()
    if not prim:
        return

    xformable = UsdGeom.Xformable(prim)
    current_matrix = xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    current_matrix = Gf.Matrix4d(current_matrix)

    # Camera forward is negative local Z axis (USD camera convention)
    forward = -Gf.Vec3d(current_matrix[2][0], current_matrix[2][1], current_matrix[2][2]).GetNormalized()

    # Convert delta from meters to stage units
    meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
    stage_delta = (delta * ZOOM_SENSITIVITY) / meters_per_unit

    # Move camera along forward
    position = current_matrix.ExtractTranslation()
    new_position = position + forward * stage_delta
    current_matrix.SetTranslateOnly(new_position)

    apply_transformation_to_prim(camera_path, current_matrix)


def handle_zoom(event):
    """Handle zoom gestures: dolly camera forward/backward."""
    message = parse_message(event)
    delta = float(message.get('delta', '0.0'))
    phase = message.get('phase', 'changed')

    if phase != 'changed':
        return

    carb.log_info(f"Zoom delta={delta}")
    _apply_zoom_to_camera(delta)


# ---------------------------------------------------------------------------
# Event routing table — MessageBus registers all of these automatically
# ---------------------------------------------------------------------------

SETEVENTNAMES = {
    "Animation": set_animation,
    "Bay": set_bay_camera,
    "PrimTap": send_prim_data,
    "Drag": handle_drag,
    "Zoom": handle_zoom,
}


class MessageBus:
    def __init__(self):
        self.event_dispatcher = carb.eventdispatcher.get_eventdispatcher()
        self.subs = []
        self.register_event_aliases()
        self.create_subscriptions()
        carb.log_info("message bus created")

    def register_event_aliases(self):
        # Register event alias for omni.kit.cloudxr.send_message
        carb_event = carb.events.type_from_string("omni.kit.cloudxr.send_message")
        omni.kit.app.register_event_alias(carb_event, "omni.kit.cloudxr.send_message")

        # Register event alias for other events
        for key, func in SETEVENTNAMES.items():
            carb_event = carb.events.type_from_string(key)
            omni.kit.app.register_event_alias(carb_event, key)

    def create_subscriptions(self):
        for key, func in SETEVENTNAMES.items():
            # Creates an observer for carb events to call a function
            self.subs.append(
                self.event_dispatcher.observe_event(
                    event_name = key,
                    on_event = func
                )
            )
        carb.log_info("subscriptions created")


    def remove_subscriptions(self):
        for sub in self.subs:
            sub.reset()
        self.subs = None

    def delete(self):
        self.remove_subscriptions()
        self.event_dispatcher = None
