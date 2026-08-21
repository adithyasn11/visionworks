# backend/app/cv/spatial_engine.py
import cv2
import numpy as np
from shapely.geometry import Point, Polygon
import logging

logger = logging.getLogger(__name__)

class SpatialEngine:
    """
    Spatial Intelligence Engine managing polygon workstation ROIs
    and Homography perspective transformations for 2D floorplan mapping.
    """
    def __init__(self, zones_config: list = None, homography_matrix=None):
        """
        zones_config: List of dicts, e.g.:
        [
            {"zone_id": "desk_01", "zone_name": "Workstation 1", "polygon": [[100, 100], [300, 100], [300, 300], [100, 300]]}
        ]
        homography_matrix: optional 3x3 camera->floorplan matrix. When absent,
            project_to_floor() falls back to a proportional frame mapping.
        """
        self.zones = []
        self.homography_matrix = (
            np.array(homography_matrix, dtype=np.float32)
            if homography_matrix is not None else None
        )
        if zones_config:
            self.load_zones(zones_config)

    def load_zones(self, zones_config: list):
        """Loads and compiles Shapely polygon boundaries for high-speed containment tests"""
        self.zones = []
        for z in zones_config:
            poly_pts = z["polygon"]
            if len(poly_pts) >= 3:
                self.zones.append({
                    "zone_id": z["zone_id"],
                    "zone_name": z.get("zone_name", z["zone_id"]),
                    "polygon": Polygon(poly_pts),
                    "raw_pts": np.array(poly_pts, dtype=np.int32)
                })

    def check_zone_containment(self, centroid: list) -> str:
        """
        Determines which desk/zone polygon contains the given (x, y) centroid point.
        Returns 'TRANSIT_ZONE' if point lies outside all registered workstation ROIs.
        """
        pt = Point(centroid[0], centroid[1])
        for zone in self.zones:
            if zone["polygon"].contains(pt):
                return zone["zone_id"]
        return "TRANSIT_ZONE"

    # Default floorplan extent, in floorplan units. The identity fallback below
    # maps a 640x480-ish frame into this box, and the heatmap normalises against
    # it, so the exact numbers only matter for keeping the aspect ratio sane.
    FLOOR_WIDTH = 1000.0
    FLOOR_HEIGHT = 700.0

    @staticmethod
    def ground_point(bbox: list) -> list:
        """
        The point where a detected person meets the floor.

        Uses the horizontal centre of the bounding box but its BOTTOM edge,
        not its centre: a homography maps the ground plane, so projecting a
        person's midriff would place them metres behind where they are standing.
        The feet are the only part of the box that actually lies on the plane
        the matrix was fitted to.
        """
        return [(bbox[0] + bbox[2]) / 2.0, float(bbox[3])]

    def project_to_floor(self, bbox: list, frame_shape) -> list:
        """
        Projects a detection's ground point onto floorplan coordinates.

        With a calibrated homography this is a true perspective correction. With
        no matrix configured — the common case, since calibration needs four
        surveyed point pairs — it falls back to a proportional mapping of the
        frame into the floorplan box. The fallback is honest rather than exact:
        it preserves where people are relative to the frame, which is what makes
        the heatmap readable, without pretending to correct for camera tilt.
        """
        point = self.ground_point(bbox)

        if self.homography_matrix is not None:
            return self.transform_point_topdown(self.homography_matrix, point)

        frame_h, frame_w = frame_shape[:2]
        if not frame_w or not frame_h:
            return point
        return [
            (point[0] / float(frame_w)) * self.FLOOR_WIDTH,
            (point[1] / float(frame_h)) * self.FLOOR_HEIGHT,
        ]

    @staticmethod
    def compute_homography_matrix(camera_points: list, floorplan_points: list) -> np.ndarray:
        """
        Computes 3x3 Homography Matrix mapping oblique camera coordinates to flat 2D floorplan coordinates.
        Requires at least 4 point correspondences.
        """
        if len(camera_points) < 4 or len(floorplan_points) < 4:
            raise ValueError("Homography calculation requires at least 4 point pairs.")

        src_pts = np.array(camera_points, dtype=np.float32)
        dst_pts = np.array(floorplan_points, dtype=np.float32)

        H, status = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        return H

    @staticmethod
    def transform_point_topdown(H: np.ndarray, point: list) -> list:
        """
        Transforms a camera coordinate [x, y] to 2D floorplan coordinate [x_floor, y_floor] using matrix H.
        """
        if H is None:
            return point

        pt = np.array([point[0], point[1], 1.0], dtype=np.float32).reshape(3, 1)
        dst = np.dot(H, pt)
        if abs(dst[2][0]) < 1e-6:
            return point
        dst /= dst[2][0]
        return [float(dst[0][0]), float(dst[1][0])]
