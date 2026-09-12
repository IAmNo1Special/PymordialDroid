"""Example: Launching an Android app with AndroidApp and AndroidController.

This script demonstrates the full app lifecycle:
1. Defining an AndroidApp with package name.
2. Registering it with an AndroidController.
3. Opening the app and waiting for readiness.
4. Capturing the screen to verify state.
5. Closing the app cleanly.

Requirements:
- A connected Android device (USB or wireless ADB).
- The target package installed on the device.
"""

from pymordialdroid import AndroidApp, AndroidController


def example_app_lifecycle(
    ip: str = "127.0.0.1",
    port: int = 5555,
    package_name: str = "com.android.settings",
) -> None:
    """Run the full open -> verify -> close lifecycle."""
    app = AndroidApp(
        app_name="Settings",
        package_name=package_name,
    )

    controller = AndroidController(ip=ip, port=port, device_name="DemoDevice")

    controller.add_app(app)

    print(f"Opening {app.app_name} ({app.package_name})...")
    opened = controller.open_app(
        app_name=app.app_name,
        package_name=app.package_name,
        timeout=30,
        wait_time=2,
    )
    print(f"open_app result: {opened}")

    screenshot = controller.capture_screen()
    if screenshot:
        print(f"Screen captured: {len(screenshot)} bytes")
        from pathlib import Path

        out = Path("screenshot_after_launch.png")
        out.write_bytes(screenshot)
        print(f"Saved to {out}")
    else:
        print("Screen capture failed.")

    print(f"Is app running: {controller.get_current_app()}")

    print(f"Closing {app.app_name}...")
    closed = controller.close_app(package_name=app.package_name)
    print(f"close_app result: {closed}")


if __name__ == "__main__":
    example_app_lifecycle()
