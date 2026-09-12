"""Example: Finding UI elements and clicking them via OCR text search and template matching.

This script demonstrates:
1. Capturing a screenshot from the device.
2. Using OCR (Tesseract) to locate text on screen.
3. Clicking the coordinates returned by OCR.
4. Checking whether specific text is present.
5. Reading all visible text lines.
6. Finding a visual element with OpenCV template matching.

Requirements:
- A connected Android device.
- Optional: a reference template image for visual matching.
"""

from pathlib import Path

from pymordial.ui.image import PymordialImage
from pymordial.ui.text import PymordialText

from pymordialdroid import AndroidController, DefaultExtractStrategy


def example_ocr_text_search(controller: AndroidController) -> None:
    """Demonstrate OCR-based text search and interaction."""
    print("Capturing screen for OCR...")
    screenshot = controller.capture_screen()
    if screenshot is None:
        print("Failed to capture screen.")
        return

    text_to_find = "Settings"

    coords = controller.ui.find_text(text_to_find, pymordial_screenshot=screenshot)
    if coords:
        print(f"Found '{text_to_find}' at {coords}")
        controller.click_coord(coords, times=1)
        print("Clicked.")
    else:
        print(f"'{text_to_find}' not found on screen.")

    strategy = DefaultExtractStrategy()
    present = controller.check_text(
        text_to_find,
        image_path=screenshot,
        case_sensitive=False,
        strategy=strategy,
    )
    print(f"check_text('{text_to_find}'): {present}")

    lines = controller.read_text(
        image_path=screenshot,
        case_sensitive=False,
        strategy=strategy,
    )
    print(f"Detected text lines: {lines}")


def example_template_matching(
    controller: AndroidController, template_path: str | Path
) -> None:
    """Demonstrate visual element finding with OpenCV template matching."""
    path = Path(template_path)
    if not path.exists():
        print(f"Template not found: {path}")
        return

    element = PymordialImage(
        label="target_icon",
        filepath=path,
        confidence=0.8,
    )

    coords = controller.find_element(element, max_tries=2)
    if coords:
        print(f"Element found at {coords}")
        controller.click_coord(coords, times=1)
        print("Clicked element.")
    else:
        print("Element not found.")


def example_pymordial_text_element(controller: AndroidController) -> None:
    """Demonstrate finding text through a PymordialText element."""
    element = PymordialText(
        label="submit_btn",
        element_text="Submit",
    )

    coords = controller.find_element(element, max_tries=2)
    if coords:
        print(f"PymordialText element found at {coords}")
        controller.click_coord(coords)
    else:
        print("PymordialText element not found.")


if __name__ == "__main__":
    controller = AndroidController(
        ip="192.168.1.50",
        port=5555,
        device_name="DemoDevice",
    )

    example_ocr_text_search(controller)
    example_pymordial_text_element(controller)
    example_template_matching(controller, template_path="assets/template.png")
