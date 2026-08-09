# backend/app/cv/anonymizer.py
import cv2
import numpy as np

class PrivacyAnonymizer:
    """
    Privacy-by-Design Frame Anonymizer.
    Applies Gaussian Blur over face and head regions of detected human bounding boxes.
    """
    @staticmethod
    def blur_face_region(frame: np.ndarray, bbox: list, blur_kernel_size: int = 25) -> np.ndarray:
        """
        Estimates top 25% of bounding box as head region and applies Gaussian blur.
        Guarantees array boundary protection.
        """
        x1, y1, x2, y2 = bbox
        frame_h, frame_w = frame.shape[:2]

        # Clamp bounding box coordinates within image bounds
        x1_c = max(0, min(frame_w - 1, x1))
        y1_c = max(0, min(frame_h - 1, y1))
        x2_c = max(0, min(frame_w - 1, x2))
        y2_c = max(0, min(frame_h - 1, y2))

        box_h = y2_c - y1_c
        if box_h <= 5 or (x2_c - x1_c) <= 5:
            return frame # Box too small to blur safely

        head_y2 = y1_c + int(box_h * 0.25)
        head_y2 = min(frame_h, max(y1_c + 1, head_y2))

        head_roi = frame[y1_c:head_y2, x1_c:x2_c]

        if head_roi.shape[0] > 0 and head_roi.shape[1] > 0:
            # Kernel size must be odd
            k_size = blur_kernel_size if blur_kernel_size % 2 != 0 else blur_kernel_size + 1
            blurred_head = cv2.GaussianBlur(head_roi, (k_size, k_size), 30)
            frame[y1_c:head_y2, x1_c:x2_c] = blurred_head

        return frame
