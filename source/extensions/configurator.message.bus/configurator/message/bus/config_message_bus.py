import omni.usd
import carb
import json

from pathlib import Path

def get_current_config():
    stage = omni.usd.get_context().get_stage()
    dataset_prim = stage.GetPrimAtPath("/World/Dataset")
    variant_set = dataset_prim.GetVariantSet("Dataset")
    variant_set_selection = variant_set.GetVariantSelection()
    return variant_set_selection

def get_configurations_of_current_file():
    current_file = get_current_config()
    # Get the configurations from the json file
    with open(Path(__file__).parent.parent.parent.parent / "data" / "configurations.json", "r") as f:
        configurations = json.load(f)
        if "Concept" in current_file:
            return configurations.get("Concept")
        elif "Ragnarok" in current_file:
            return configurations.get("Ragnarok")
        else:
            carb.log_error("No configurations found for this file")
            return None


def parse_message(event):
    carb.log_info("Messaged recieved")
    payload = event.payload
    message = json.loads(payload['message'])
    return message

def update_variant_set(prim_path, variant_set_name, variant_set_selection):
    stage = omni.usd.get_context().get_stage()
    prim = stage.GetPrimAtPath(prim_path)
    if prim:
        variant_set = prim.GetVariantSet(variant_set_name)
        if variant_set:
            variant_set.SetVariantSelection(variant_set_selection)
    message = "VariantSet: " + variant_set_name + " Updated to: " + variant_set_selection
    send_message(message)

def grab_update_information(event, configuration_type):
    message = parse_message(event)
    carb.log_info(f"Message recieved: {message}")
    config_message = message.get(configuration_type)
    configurations = get_configurations_of_current_file()
    if not configurations:
        return
    variant_set_info = configurations.get("VariantSet", {}).get(configuration_type)
    variant_set_selection = configurations.get(configuration_type, {}).get(config_message)
    if not variant_set_info or not variant_set_selection:
        carb.log_error(f"Missing configuration for {configuration_type}: {config_message}")
        return
    update_variant_set(variant_set_info.get("PrimPath"), variant_set_info.get("VariantSetName"), variant_set_selection)

def set_option(event):
    grab_update_information(event, "Option")

def set_style(event):
    grab_update_information(event, "Style")

def set_accessory(event):
    grab_update_information(event, "Accessory")

def send_message(return_message):
    omni.kit.app.queue_event("omni.kit.cloudxr.send_message", payload={"message": return_message})

SETEVENTNAMES = {
    "Option": set_option,
    "Accessory": set_accessory,
    "Style": set_style
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
