"""
Document Object Detectors — identifies tables, stamps/seals, signatures,
and watermarks in document images using OpenCV contour analysis and
morphological operations.

Each detector returns a list of ``DetectedObject`` instances with bounding
boxes and confidence scores.
"""

from __future__ import annotations

import logging

import numpy as np

from app.ocr_pipeline.models import DetectedObject, ObjectType, PageDetectionResult

logger = logging.getLogger(__name__)

# Minimum contour area (in pixels) to consider for detection
_MIN_CONTOUR_AREA = 500

# Aspect ratio thresholds for stamp detection (roughly circular)
_STAMP_MIN_ASPECT = 0.7
_STAMP_MAX_ASPECT = 1.4


class PageDetector:
    """Detects tables, stamps, signatures, and watermarks on a document page.

    Usage::

        detector = PageDetector()
        result = detector.detect_all(image_array)
        print(f"Tables: {result.table_count}, Stamps: {result.has_stamp}")
    """

    @staticmethod
    def detect_all(image: np.ndarray) -> PageDetectionResult:
        """Run all detectors on the image and return a combined result.

        Args:
            image: Input image as NumPy array (BGR color format preferred).

        Returns:
            A :class:`PageDetectionResult` with all detections.
        """
        import cv2

        # Keep the original BGR image for stamp detection (needs color)
        is_color = len(image.shape) == 3
        bgr_image = image if is_color else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)

        objects: list[DetectedObject] = []
        has_table = False
        has_stamp = False
        has_signature = False
        has_watermark = False
        table_count = 0

        # Run each detector
        tables = PageDetector._detect_tables(gray)
        for t in tables:
            objects.append(t)
            table_count += 1
        has_table = table_count > 0

        # Pass BGR image for stamp detection (needs color info)
        stamps = PageDetector._detect_stamps(bgr_image, gray)
        for s in stamps:
            objects.append(s)
        has_stamp = len(stamps) > 0

        signatures = PageDetector._detect_signatures(gray)
        for s in signatures:
            objects.append(s)
        has_signature = len(signatures) > 0

        watermarks = PageDetector._detect_watermarks(gray)
        for w in watermarks:
            objects.append(w)
        has_watermark = len(watermarks) > 0

        return PageDetectionResult(
            objects=objects,
            has_table=has_table,
            has_stamp=has_stamp,
            has_signature=has_signature,
            has_watermark=has_watermark,
            table_count=table_count,
        )

    # ------------------------------------------------------------------
    # Table Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_tables(gray: np.ndarray) -> list[DetectedObject]:
        """Detect table structures using horizontal and vertical line detection."""
        import cv2

        detected: list[DetectedObject] = []
        h, w = gray.shape

        # Threshold to binary
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Detect horizontal lines
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 30, 1), 1))
        horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)

        # Detect vertical lines
        vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 30, 1)))
        vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)

        # Combine to find table grid
        grid = cv2.add(horizontal, vertical)
        grid = cv2.dilate(grid, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=2)

        # Find contours of table regions
        contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < _MIN_CONTOUR_AREA * 10:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            # Filter out full-page contours (not tables)
            if cw > w * 0.95 and ch > h * 0.95:
                continue
            confidence = min(1.0, area / (h * w * 0.5))
            detected.append(
                DetectedObject(
                    type=ObjectType.TABLE,
                    confidence=confidence,
                    bbox=[float(x), float(y), float(x + cw), float(y + ch)],
                )
            )

        return detected

    # ------------------------------------------------------------------
    # Stamp / Seal Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_stamps(bgr: np.ndarray, gray: np.ndarray) -> list[DetectedObject]:
        """Detect circular/oval stamps and seals using color segmentation
        on the BGR image combined with Hough Circle transform on grayscale.

        Args:
            bgr: Original BGR color image (needed for color-based detection).
            gray: Grayscale version (needed for HoughCircles).
        """
        import cv2

        detected: list[DetectedObject] = []

        # ---- Strategy 1: Color-based (red/blue stamps) ----
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        # Red color range (stamps are commonly red)
        lower_red1 = np.array([0, 70, 50])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([170, 70, 50])
        upper_red2 = np.array([180, 255, 255])
        red_mask = cv2.inRange(hsv, lower_red1, upper_red1) | cv2.inRange(hsv, lower_red2, upper_red2)

        # Blue color range (some stamps/seals are blue)
        lower_blue = np.array([100, 70, 50])
        upper_blue = np.array([130, 255, 255])
        blue_mask = cv2.inRange(hsv, lower_blue, upper_blue)

        combined_mask = cv2.bitwise_or(red_mask, blue_mask)
        combined_mask = cv2.dilate(combined_mask, None, iterations=2)

        contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < _MIN_CONTOUR_AREA:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            aspect = cw / max(ch, 1)
            # Stamps are roughly circular/square (aspect ratio near 1)
            if _STAMP_MIN_ASPECT < aspect < _STAMP_MAX_ASPECT:
                confidence = min(0.9, area / 50_000)
                detected.append(
                    DetectedObject(
                        type=ObjectType.STAMP,
                        confidence=confidence,
                        bbox=[float(x), float(y), float(x + cw), float(y + ch)],
                    )
                )

        # ---- Strategy 2: HoughCircles on grayscale (catches non-color stamps) ----
        try:
            circles = cv2.HoughCircles(
                gray,
                cv2.HOUGH_GRADIENT,
                dp=1.2,
                minDist=50,
                param1=50,
                param2=30,
                minRadius=10,
                maxRadius=200,
            )
            if circles is not None:
                circles = circles[0]
                for x, y, r in circles:
                    conf = min(0.7, r / 150.0)
                    detected.append(
                        DetectedObject(
                            type=ObjectType.STAMP,
                            confidence=conf,
                            bbox=[float(x - r), float(y - r), float(x + r), float(y + r)],
                        )
                    )
        except Exception:
            pass

        return detected

    # ------------------------------------------------------------------
    # Signature Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_signatures(gray: np.ndarray) -> list[DetectedObject]:
        """Detect signatures by looking for cursive/handwritten text regions
        with distinctive stroke characteristics."""
        import cv2

        detected: list[DetectedObject] = []
        h, w = gray.shape

        # Apply morphological operations to isolate handwriting strokes
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Remove large objects (text blocks, images)
        kernel_large = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5))
        large_removed = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_large)

        # Remaining small connected components could be signatures
        # Dilate to connect nearby strokes
        kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        dilated = cv2.dilate(large_removed, kernel_small, iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < _MIN_CONTOUR_AREA or area > h * w * 0.3:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            aspect = cw / max(ch, 1)

            # Signatures are typically wide and short (horizontal cursive)
            if 1.5 < aspect < 8.0 and ch < h * 0.15:
                confidence = min(1.0, area / (h * w * 0.05))
                detected.append(
                    DetectedObject(
                        type=ObjectType.SIGNATURE,
                        confidence=confidence,
                        bbox=[float(x), float(y), float(x + cw), float(y + ch)],
                    )
                )

        return detected

    # ------------------------------------------------------------------
    # Watermark Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_watermarks(gray: np.ndarray) -> list[DetectedObject]:
        """Detect watermarks by finding semi-transparent, repetitive patterns
        or text that is lighter than the surrounding area."""
        import cv2

        detected: list[DetectedObject] = []

        # Watermarks are often lighter than the background
        # Invert and threshold to capture faint text
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, binary = cv2.threshold(blurred, 200, 255, cv2.THRESH_BINARY)

        # Watermarks often have low contrast — detect using edge detection
        edges = cv2.Canny(blurred, 50, 150)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        dilated = cv2.dilate(edges, kernel, iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < _MIN_CONTOUR_AREA * 2:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)

            # Watermarks are often in the center or repeated across the page
            # Check if the region brightness is close to background
            roi = blurred[y : y + ch, x : x + cw]
            mean_brightness = cv2.mean(roi)[0] if roi.size > 0 else 0

            if 180 < mean_brightness < 240:  # Faint, near-white
                confidence = min(0.8, 1.0 - mean_brightness / 255.0)
                detected.append(
                    DetectedObject(
                        type=ObjectType.WATERMARK,
                        confidence=confidence,
                        bbox=[float(x), float(y), float(x + cw), float(y + ch)],
                    )
                )

        return detected
