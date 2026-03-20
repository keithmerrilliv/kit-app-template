import omni.ext
import omni.kit.commands
import omni.kit.app
import carb
import omni
import json
from omni.kit.viewport.utility import get_active_viewport_window

from .util import *

from pxr import UsdGeom
import omni.kit.xr.core

# Any class derived from `omni.ext.IExt` in top level module (defined in `python.modules` of `extension.toml`) will be
# instantiated when extension gets enabled and `on_startup(ext_id)` will be called. Later when extension gets disabled
# on_shutdown() is called.
class ProxyPrimSample(omni.ext.IExt):
    # ext_id is current extension id. It can be used with extension manager to query additional information, like where
    # this extension is located on filesystem.
    def __init__(self):
        super().__init__()
        self.event_dispatcher = None
        self.observed_events = []
        self._viewport_interface = None
        self._camera_sub = None
        self._set_event_alias(["omni.kit.cloudxr.send_message", "initial_prim_path", "write_prim_transformation_extension", "request_camera_transform", "discover_prims"])
        self._object_change_sub = None
        self.identity_matrix_list =  [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0
        ]
        self.flag_camera_transform = self.identity_matrix_list
        self.pre_camera_transform = self.identity_matrix_list
        self.start_camera_transform = self.identity_matrix_list
        self.end_camera_transform = self.identity_matrix_list
        self.new_start_camera_transform =self.identity_matrix_list
        self._xr_mode_enabled = False

    def _set_event_alias(self, event_alias_list: list):
        """Set the event alias for the extension.

        Args:
            event_alias_list (list): List of event alias to set.
        """
        for event_alias in event_alias_list:
            carb_event = carb.events.type_from_string(event_alias)
            omni.kit.app.register_event_alias(carb_event, event_alias)

    @staticmethod
    def on_path_push(event: carb.events.IEvent):
        """Respond to path_event: decode the prim paths, obtain the bbox and position info for each prim path, and send it to the client.
                   We need to declare it as static method, otherwise it cannot be called by the event subscription.

        Args:
            event: path_event.

        Return:
            Send the message type and bbox, position info to the client.
        """
        state = json.loads(event.payload["message"])
        prim_paths = state["PrimPath"]
        prim_path_list = prim_paths.split(",")
        return_message = {"Type": "initial_prims_setup", "BoundingBox": ""}

        # Get stage up axis
        up_axis = get_scene_up_axis()
        is_z_up = up_axis == UsdGeom.Tokens.z
        carb.log_warn(f"[DIAG] Prim bbox request: up_axis={up_axis} is_z_up={is_z_up} UsdGeom.Tokens.z={UsdGeom.Tokens.z}")

        # Calculate the bounding box and position info for each prim path
        for prim_path in prim_path_list:
            prim, box_string = calculate_bounding_box_info(prim_path, is_z_up)
            if prim:
                # Separate the bounding box and location information of different prims by ;
                return_message["BoundingBox"] = (
                    return_message["BoundingBox"]
                    + prim_path
                    + ", "
                    + box_string
                    + ";"
                )

        # Send the measured bbox and position info to the client
        send_message_to_client(return_message)

    @staticmethod
    def on_discover_prims(event: carb.events.IEvent):
        """Respond to a client request to discover interactive prims in the scene.
        Scans the stage for Mesh/Xform prims under /World (up to 2 levels deep)
        and sends their bbox data using the existing initial_prims_setup format.
        """
        import omni.usd
        from pxr import Usd, UsdGeom

        stage = omni.usd.get_context().get_stage()
        if not stage:
            return

        discovered = []
        root = stage.GetPrimAtPath("/World")
        if not root:
            root = stage.GetPseudoRoot()

        def scan_prims(prim, depth=0, max_depth=2):
            if depth > max_depth:
                return
            for child in prim.GetChildren():
                if child.GetName().startswith("_") or child.GetName().startswith("xr"):
                    continue
                if child.IsA(UsdGeom.Xformable) and not child.IsA(UsdGeom.Camera):
                    if child.IsA(UsdGeom.Mesh) or (child.IsA(UsdGeom.Xform) and child.GetChildren()):
                        discovered.append(str(child.GetPath()))
                        if len(discovered) >= 10:
                            return
                if len(discovered) < 10:
                    scan_prims(child, depth + 1, max_depth)

        scan_prims(root)

        carb.log_warn(f"[discover_prims] Found {len(discovered)} prims, sending bbox data")

        # Send bbox data using existing initial_prims_setup format
        is_z_up = get_scene_up_axis() == UsdGeom.Tokens.z
        for prim_path in discovered:
            prim, box_string = calculate_bounding_box_info(prim_path, is_z_up)
            if prim and box_string:
                return_message = {
                    "Type": "initial_prims_setup",
                    "BoundingBox": prim_path + ", " + box_string + ";"
                }
                send_message_to_client(return_message)
                carb.log_warn(f"  Sent bbox for: {prim_path}")

    @staticmethod
    def on_camera_transform_request(event: carb.events.IEvent):
        """Respond to a client request for the current camera transform."""
        camera_transform = get_camera_transform()
        if camera_transform:
            send_message_to_client({
                "Type": "camera_transform",
                "Transform": camera_transform
            })

    @staticmethod
    def on_transformation_push(event: carb.events.IEvent):
        """Respond to manipulation interactions: decode the prim path and the transformation matrix, set the transformation matrix of the prim.
                   We need to declare it as static method, otherwise it cannot be called by the event subscription.

        Args:
                event: transformation_event.

        Return:
                Set the prim's transformation matrix
        """
        state = json.loads(event.payload["message"])
        prim_path = state["PrimPath"]
        prim_transformation_matrix = state["Value"]

        # Get stage up axis
        stage = omni.usd.get_context().get_stage()
        is_z_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z

        # Convert the transformation matrix from client format to USD format
        transformation_matrix = convert_transformation_matrix(prim_transformation_matrix, is_z_up)

        # Apply the transformation to the prim
        apply_transformation_to_prim(prim_path, transformation_matrix)

    def _on_objects_changed(self, prim_path):
        if not self._xr_mode_enabled:
            return None

        anchor_name_settings_path = "/xrstage/profile/ar/customAnchor"
        anchor_mode_settings_path = "/persistent/xr/profile/ar/anchorMode"

        # Get current anchor path from settings
        settings = carb.settings.get_settings()
        anchor_mode = settings.get(anchor_mode_settings_path)
        if anchor_mode == "custom anchor":
            anchor_path = settings.get(anchor_name_settings_path)

        elif anchor_mode == "active camera":
            viewport_window = get_active_viewport_window()
            if not viewport_window:
                anchor_path = ""
            else:
                viewport_api = viewport_window.viewport_api
                anchor_path = viewport_api.get_active_camera()
                if anchor_path == "/_xr/stage/xrCamera":
                    self.pre_camera_transform = get_prim_full_transform("/_xr/stage/xrCamera")
                    self.start_camera_transform =  self.new_start_camera_transform
                else:
                    self.end_camera_transform =  self.pre_camera_transform
                    self.new_start_camera_transform =  self.pre_camera_transform
                anchor_path = "/_xr/stage/xrSpaceOrigin"
        elif anchor_mode == "scene origin":
            anchor_path = "/World"
        else:
            return None

        matrix = get_prim_full_transform(anchor_path)
        if matrix is None:
            return None
        matrix[-4] = matrix[-4] + self.end_camera_transform[-4] - self.start_camera_transform[-4]
        matrix[-3] = matrix[-3] + self.end_camera_transform[-3] - self.start_camera_transform[-3]
        matrix[-2] = matrix[-2] + self.end_camera_transform[-2] - self.start_camera_transform[-2]

        if self.flag_camera_transform == matrix:
            return None
        self.flag_camera_transform = matrix

        # Send the anchor transform to the client
        self._send_to_client({
            "Type": "pp_camera_transform",
            "Transform": matrix
        })


    def on_startup(self, ext_id: str):
        def on_xr_enable(event: carb.events.IEvent):
            self._xr_mode_enabled = True
        def on_xr_disable(event: carb.events.IEvent):
            self._xr_mode_enabled = False
        xr_core = omni.kit.xr.core.XRCore.get_singleton()
        message_bus: carb.events.IEventStream = xr_core.get_message_bus()
        message_type: int = carb.events.type_from_string("xr.enable")
        disable_message_type: int = carb.events.type_from_string("xr.disable")

        self.subscription1: carb.events.ISubscription = message_bus.create_subscription_to_pop_by_type(message_type, on_xr_enable)
        self.subscription2: carb.events.ISubscription = message_bus.create_subscription_to_pop_by_type(disable_message_type, on_xr_disable)
        # Get the event dispatcher
        self.event_dispatcher = carb.eventdispatcher.get_eventdispatcher()

        # Subscribe to the events
        self.observed_events.append(self.event_dispatcher.observe_event(
            event_name="initial_prim_path",
            on_event=self.on_path_push
        ))
        self.observed_events.append(self.event_dispatcher.observe_event(
            event_name="write_prim_transformation_extension",
            on_event=self.on_transformation_push
        ))
        self.observed_events.append(self.event_dispatcher.observe_event(
            event_name="request_camera_transform",
            on_event=self.on_camera_transform_request
        ))
        self.observed_events.append(self.event_dispatcher.observe_event(
            event_name="discover_prims",
            on_event=self.on_discover_prims
        ))

        # Subscribe to camera changes
        viewport_window = get_active_viewport_window()
        if viewport_window:
            viewport_api = viewport_window.viewport_api
            self._camera_sub = viewport_api.subscribe_to_view_change(
                self._on_camera_changed
            )

        print("[omni.sample.proxyprimmani] Proxy Prim Manipulation Sample startup")

    def on_shutdown(self):
        """Called when the extension is disabled"""
        for event in self.observed_events:
            event.reset()
        self.observed_events = []

        # Remove viewport subscription
        if self._camera_sub:
            self._camera_sub = None

        self._object_change_sub = None
        self.subscription1 = None
        self.subscription2 = None

        print("[omni.sample.proxyprimmani] Proxy Prim Manipulation Sample shutdown")

    def _on_camera_changed(self, viewport_api):
        """Handle camera view change events"""
        camera_path = str(viewport_api.camera_path)
        carb.log_info(f"Camera changed to: {camera_path}")
        camera_transform = get_camera_transform()
        if camera_transform:
            # Send camera position to client
            self._send_to_client({
                "Type": "camera_transform",
                "Transform": camera_transform
            })
        self._on_objects_changed(camera_path)

    def _send_to_client(self, message: dict):
        """Send a message to the client using the CloudXR message bus.

        Args:
            message (dict): Message to send to the client
        """
        send_message_to_client(message)