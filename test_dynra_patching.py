import sys

from dynra_core import NamespaceManager, patch_class


def test_imported_function_reference_is_patched_in_place():
    manager = NamespaceManager()
    origin_name = "dynra_test_origin"
    consumer_name = "dynra_test_consumer"
    origin = manager.md(origin_name)
    consumer = manager.md(consumer_name)

    try:
        exec("def value(): return 1", origin.__dict__)
        exec(f"from {origin_name} import value", consumer.__dict__)
        imported_reference = consumer.value

        exec("def value(): return 101", origin.__dict__)
        manager._propagate_update("value", origin.value, origin_name)

        assert consumer.value is imported_reference
        assert consumer.value() == 101
    finally:
        sys.modules.pop(origin_name, None)
        sys.modules.pop(consumer_name, None)


def test_class_patch_updates_existing_instance_state():
    class SessionV1:
        def __init__(self, user):
            self.user = user

        def label(self):
            return self.user

    session = SessionV1("Ada")

    class SessionV2:
        def label(self):
            return f"{self.user}:{self.status}"

        def __dynra_update__(self):
            self.status = "active"

    assert patch_class(SessionV1, SessionV2)
    assert session.status == "active"
    assert session.label() == "Ada:active"
