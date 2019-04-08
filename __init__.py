from collections import defaultdict

from mycroft.skills.common_iot_skill import CommonIoTSkill,\
    IoTRequest, Thing, Action, Attribute
from mycroft.util.log import getLogger


from .homeassistant.client import HomeAssistantClient


__author__ = 'robconnolly, btotharye, nielstron'
LOGGER = getLogger(__name__)

# Common Strings
_ACTIONS = "actions"
_ATTRIBUTES = "attributes"
_BRIGHTNESS = "brightness"
_CLIMATE = "climate"
_DOMAIN = "domain"
_ENTITY_ID = "entity_id"
_LIGHT = "light"
_SERVICE = "service"
_STATES = "states"
_SWITCH = "switch"


_THING_TO_DOMAIN = {
    Thing.LIGHT: _LIGHT,
    Thing.THERMOSTAT: _CLIMATE,
    Thing.SWITCH: _SWITCH,
}


_DOMAIN_TO_THING = {v: k for k, v in _THING_TO_DOMAIN.items()}


_DOMAINS = {
    _CLIMATE: {
        _ACTIONS: {
            Action.ON,
            Action.OFF,
            Action.TOGGLE,
            Action.INCREASE,
            Action.DECREASE,
        },
        _ATTRIBUTES: {
            Attribute.TEMPERATURE,
            Attribute.HEAT,
            Attribute.AIR_CONDITIONING,
        },
    },
    _LIGHT: {
        _ACTIONS: {
            Action.ON,
            Action.OFF,
            Action.TOGGLE,
            Action.INCREASE,
            Action.DECREASE,
        },
        _ATTRIBUTES: {
            Attribute.BRIGHTNESS,
        },

    },
    _SWITCH: {
        _ACTIONS: {
            Action.ON,
            Action.OFF,
            Action.TOGGLE,
        },
        _ATTRIBUTES: {

        },
    },
}


#TODO Make these settings
_BRIGHTNESS_STEP = 20
_TEMPERATURE_STEP = 2


class HomeAssistantSkill(CommonIoTSkill):

    def __init__(self):
        super().__init__(name="HomeAssistantSkill")
        self._client: HomeAssistantClient = None
        self._entities = dict()

    def initialize(self):
        self.settings.set_changed_callback(self.on_websettings_changed)
        self._setup()
        self._entities = self._build_entities_map(self._client.entities())
        self.register_entities_and_scenes()

    def _build_entities_map(self, entities: dict):
        results = defaultdict(list)
        for id, name in entities.items():
            if self._domain(id) in _DOMAIN_TO_THING:
                results[name].append(id)
        return results

    def on_websettings_changed(self):
        # Force a setting refresh after the websettings changed
        # Otherwise new settings will not be regarded
        self._force_setup()

    def _setup(self):
        portnumber = int(self.settings.get('portnum', 8123))
        self._client = HomeAssistantClient(
            token=self.settings.get('token'),
            hostname=self.settings.get('hostname', 'localhost'),
            port=portnumber,
            ssl=self.settings.get('ssl', False),
            verify=self.settings.get('verify', True)
        )

    def _force_setup(self):
        LOGGER.debug('Creating a new HomeAssistant-Client')
        self._setup()

    def _domain(self, entity_id: str):
        if entity_id is None:
            return None
        return entity_id[:entity_id.index('.')]

    def stop(self):
        pass

    def get_entities(self):
        return self._entities.keys()

    def run_request(self, request: IoTRequest, callback_data: dict):
        self._client.run_services(**callback_data)

    def can_handle(self, request: IoTRequest):
        action = request.action
        thing = request.thing
        entity = request.entity
        attribute = request.attribute

        if not thing and not entity:
            return False, None

        if thing and thing not in _THING_TO_DOMAIN:
            return False, None

        if entity:  # TODO refactor this into its own function
            possible_ids = self._entities[entity]
            if not possible_ids:
                return False, None

            filtered_entities = []
            for id in possible_ids:
                domain_of_id = self._domain(id)
                if action in _DOMAINS[domain_of_id][_ACTIONS] and \
                        (not attribute or attribute in _DOMAINS[domain_of_id][_ATTRIBUTES]):
                        filtered_entities.append(id)

            if len(filtered_entities) != 1:
                return False, None
            entity = filtered_entities[0]

        domain = self._domain(entity) if entity else None
        if domain:
            if domain not in _DOMAIN_TO_THING:
                return False, None

            if not thing:
                thing = _DOMAIN_TO_THING[domain]

            if thing != _DOMAIN_TO_THING[domain]:
                return False, None

        if thing == Thing.LIGHT:
            return self._can_handle_lights(action, attribute, entity)

        if thing == Thing.THERMOSTAT:
            return self._can_handle_thermostat(action, attribute, entity)

        # TODO - handle switches

        return False, None

    def _can_handle_lights(self, action: Action, attribute: Attribute, entity_id: str):
        simple_actions = {Action.TOGGLE: "toggle", Action.ON: "turn_on", Action.OFF: "turn_off"}
        if action in simple_actions:
            data = {_DOMAIN: _LIGHT, _SERVICE: simple_actions[action]}
            states = [dict()]
            if entity_id:
                states[0][_ENTITY_ID] = entity_id
            data[_STATES] = states
            return True, data

        states = self._client.get_states(entity_id)

        if not entity_id:
            states = (s for s in states if s[_ENTITY_ID].startswith(_LIGHT))

        if action in {Action.INCREASE, Action.DECREASE} and attribute in {Attribute.BRIGHTNESS, None}:
            states = [s for s in states if _BRIGHTNESS in s[_ATTRIBUTES]]

            if states:
                step = _BRIGHTNESS_STEP if action == Action.INCREASE \
                    else -1 * _BRIGHTNESS_STEP
                states = [{_ENTITY_ID: s[_ENTITY_ID],
                           _BRIGHTNESS: _adjust_brightness(s, step)}
                          for s in states]

                return True, {_DOMAIN : _LIGHT,
                              _SERVICE: "turn_on",
                              _STATES: states}

        return False, None

    def _can_handle_thermostat(self, action: Action, attribute: Attribute, entity_id: str):
        simple_actions = {Action.ON: "turn_on", Action.OFF: "turn_off"}
        if action in simple_actions:
            state = {_DOMAIN: _CLIMATE, _SERVICE: simple_actions[action]}
            if entity_id:
                state[_ENTITY_ID] = entity_id
            return True, [state]

        if action in (Action.INCREASE, Action.DECREASE):
            states = self._client.get_states(entity_id)

            if not entity_id:
                states = (s for s in states if s[_ENTITY_ID].startswith(_CLIMATE))

            states = [s for s in states if any([a.startswith('target_temp') for a in s[_ATTRIBUTES]])]

            if states:
                step = _TEMPERATURE_STEP if action == Action.INCREASE \
                    else -1 * _TEMPERATURE_STEP

                states = [_adjust_temperature(s, step) for s in states]
                LOGGER.info(states)

                return True, {_DOMAIN : _CLIMATE,
                              _SERVICE: "set_temperature",
                              _STATES: states}
        return False, None


def _adjust_brightness(current_state, adjustment):
    value = current_state[_ATTRIBUTES][_BRIGHTNESS] + adjustment
    if value > 254:
        value = 254
    if value < 0:
        value = 0
    return value


def _adjust_temperature(current_state, adjustment):
    attributes = current_state[_ATTRIBUTES].items()
    data = {k: v + adjustment for k, v in attributes if k.startswith('target_temp')}
    entity_id = current_state[_ENTITY_ID]
    data[_ENTITY_ID] = entity_id
    return data

def create_skill():
    return HomeAssistantSkill()
