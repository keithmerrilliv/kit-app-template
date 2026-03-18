import omni.usd
import carb
import json
from omni.kit.viewport.utility import get_active_viewport_window


BAY_SCOPE_PATH = "/World/Cameras/bays/"

def set_bay_camera(event):
    message = parse_message(event)
    bay_number = message.get('bay')
    carb.log_info(f"Bay number: {BAY_SCOPE_PATH + bay_number}")
    viewport_api = get_active_viewport_window().viewport_api
    if viewport_api:
        viewport_api.camera_path = BAY_SCOPE_PATH + bay_number
    else:
        carb.log_info("viewport doesnt exist")

def parse_message(event):
    carb.log_info("Messaged recieved")
    payload = event.payload
    message = json.loads(payload['message'])
    return message


def set_animation(event):
    message = parse_message(event)
    anim_action = message.get('animationAction')
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
    pass

def send_message(return_message):
    pass

SETEVENTNAMES = {
    "Animation": set_animation,
    "Bay": set_bay_camera,
    "PrimTap": send_prim_data
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
        self._event_dispatcher = None
