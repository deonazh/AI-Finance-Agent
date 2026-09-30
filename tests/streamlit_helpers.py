"""Bridge stateful tabs until Streamlit AppTest serializes tab-container widgets.

Streamlit 1.63 renders these blocks but its get_widget_state only handles Widget
instances. A browser sends the tab's selected label on every interaction; emulate
that transport here. Real-browser tests separately verify tab clicks and layout.
"""
from unittest.mock import patch
from streamlit.proto.WidgetStates_pb2 import WidgetState
from streamlit.testing.v1 import element_tree


def stateful_tabs():
    original = element_tree.get_widget_state
    def serialize(node):
        if node.type == "tab_container" and node.proto.tab_container.id:
            identifier = node.proto.tab_container.id
            return WidgetState(id=identifier, string_value=node.root.session_state[identifier])
        return original(node)
    return patch.object(element_tree, "get_widget_state", serialize)
