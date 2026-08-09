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
    def __init__(self, zones_config: list = None):
        """
        zones_config: List of dicts, e.g.:
        [
            {"zone_id": "desk_01", "zone_name": "Workstation 1", "polygon": [[100, 100], [300, 100], [300, 300], [100, 300]]}
        ]
        """
        self.zones = []
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
