"""Android application model with package lifecycle and ready state detection."""

from typing import TYPE_CHECKING

from pymordial.core.app import PymordialApp
from pymordial.core.screen import PymordialScreen
from pymordial.core.state_machine import AppState
from pymordial.ui.element import PymordialElement

if TYPE_CHECKING:
    from pymordial.core.controller import PymordialController


class AndroidApp(PymordialApp):
    """Represents an Android application with lifecycle management.

    The controller reference is automatically attached when registered with
    a PymordialController instance via controller.add_app(...).

    Attributes:
        app_name: The display name of the application.
        package_name: The Android package name (e.g. 'com.example.app').
        screens: Dictionary mapping screen names to PymordialScreen instances.
        ready_element: Optional UI element indicating the app has fully loaded.
    """

    def __init__(
        self,
        app_name: str,
        package_name: str,
        screens: dict[str, PymordialScreen] | None = None,
        ready_element: PymordialElement | None = None,
    ) -> None:
        if not app_name:
            raise ValueError("app_name must be a non-empty string")
        if not package_name:
            raise ValueError("package_name must be a non-empty string")

        super().__init__(
            app_name=app_name,
            screens=screens if screens is not None else {},
            ready_element=ready_element,
        )
        self.package_name: str = package_name
        self.pymordial_controller: PymordialController | None = None

    def check_ready(self, max_tries: int | None = None) -> bool:
        """Checks if the ready_element is visible and transitions to READY state."""
        if not self.ready_element or not self.pymordial_controller:
            return False

        if self.app_state.current_state != AppState.LOADING:
            return False

        try:
            if self.pymordial_controller.is_element_visible(
                self.ready_element, max_tries=max_tries
            ):
                self.app_state.transition_to(AppState.READY)
                return True
        except Exception:
            pass

        return False

    def is_open(self) -> bool:
        """Checks if the app is in the READY state."""
        return self.app_state.current_state == AppState.READY

    def is_loading(self) -> bool:
        """Checks if the app is in the LOADING state."""
        return self.app_state.current_state == AppState.LOADING

    def is_closed(self) -> bool:
        """Checks if the app is in the CLOSED state."""
        return self.app_state.current_state == AppState.CLOSED

    def open(self, timeout: int = 60, wait_time: int = 10) -> bool:
        """Launches the application via the controller."""
        if not self.pymordial_controller:
            raise RuntimeError("Cannot open app: no PymordialController attached.")

        return self.pymordial_controller.open_app(
            app_name=self.app_name,
            package_name=self.package_name,
            timeout=timeout,
            wait_time=wait_time,
        )

    def close(self, timeout: int = 30, wait_time: int = 2) -> bool:
        """Closes the application via the controller."""
        if not self.pymordial_controller:
            raise RuntimeError("Cannot close app: no PymordialController attached.")

        return self.pymordial_controller.close_app(
            package_name=self.package_name,
            timeout=timeout,
            wait_time=wait_time,
        )

    def is_running(self) -> bool:
        """Checks if the application is currently running on the device."""
        if not self.pymordial_controller:
            return False

        bridge = getattr(self.pymordial_controller, "bridge", None)
        if bridge and hasattr(bridge, "is_app_running"):
            return bridge.is_app_running(self.package_name)
        return False


__all__ = [
    "AndroidApp",
]
