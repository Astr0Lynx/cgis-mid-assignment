"""WPS process: on-demand NDVI from Bhoonidhi Resourcesat LISS-III scenes."""
import json
import math
import os
from datetime import datetime

import numpy as np
from pywps import BoundingBoxInput, ComplexOutput, Format, LiteralInput, Process
from pywps.app.exceptions import ProcessError

from app.scene import find_scenes, write_cog

GEOTIFF = Format("image/tiff; subtype=geotiff", extension=".tif")
JSON = Format("application/json", extension=".json")


MAX_PIXELS = int(os.environ.get("MAX_PIXELS", 16_000_000))  # keeps memory bounded (~100 MB per float32 band)


def _approx_pixels(bbox, resolution_m: float = 24.0) -> float:
    """Pixel count of a lon/lat bbox at LISS-III resolution (flat-earth estimate)."""
    minx, miny, maxx, maxy = bbox
    width_m = (maxx - minx) * 111_320 * math.cos(math.radians((miny + maxy) / 2))
    height_m = (maxy - miny) * 110_574
    return width_m * height_m / resolution_m ** 2


class Ndvi(Process):
    def __init__(self):
        inputs = [
            BoundingBoxInput(
                "bbox", "Area of interest",
                crss=["EPSG:4326"], min_occurs=1, max_occurs=1,
                abstract="Bounding box in WGS84. Axis order is lat,lon (EPSG:4326 per OGC): miny,minx,maxy,maxx.",
            ),
            LiteralInput(
                "date", "Acquisition date", data_type="string", min_occurs=0, max_occurs=1,
                abstract="ISO date (YYYY-MM-DD) of the scene to use. Defaults to the newest scene covering the bbox.",
            ),
        ]
        outputs = [
            ComplexOutput("ndvi", "NDVI raster", supported_formats=[GEOTIFF], as_reference=True,
                          abstract="Float32 Cloud Optimized GeoTIFF, NaN = no data, in the scene's UTM CRS."),
            ComplexOutput("stats", "NDVI statistics", supported_formats=[JSON],
                          abstract="Scene id, acquisition date, and min/mean/max/valid fraction of the output."),
        ]
        super().__init__(
            self._handler,
            identifier="ndvi",
            title="NDVI from Bhoonidhi LISS-III",
            abstract=(
                "Computes the Normalised Difference Vegetation Index for a bounding box from "
                "Resourcesat-2 LISS-III scenes published on Bhoonidhi (ISRO/NRSC). "
                "v1 uses at-sensor radiance (no atmospheric or solar-irradiance correction)."
            ),
            keywords=["NDVI", "Bhoonidhi", "Resourcesat", "LISS-III"],
            inputs=inputs, outputs=outputs,
            store_supported=True, status_supported=True,
        )

    def _handler(self, request, response):
        # PyWPS hands EPSG:4326 boxes over in axis order lat,lon
        miny, minx, maxy, maxx = (float(v) for v in request.inputs["bbox"][0].data)
        bbox = (minx, miny, maxx, maxy)
        if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
            raise ProcessError("Invalid bbox: expected miny,minx,maxy,maxx in EPSG:4326 with min < max")

        pixels = _approx_pixels(bbox)
        if pixels > MAX_PIXELS:
            raise ProcessError(
                f"bbox too large: about {pixels / 1e6:.0f} Mpixel at 24 m, limit is {MAX_PIXELS / 1e6:.0f} Mpixel "
                f"(roughly {MAX_PIXELS ** 0.5 * 24 / 1000:.0f} x {MAX_PIXELS ** 0.5 * 24 / 1000:.0f} km)"
            )

        response.update_status("Searching scenes", 10)
        scenes = find_scenes(bbox)
        if "date" in request.inputs:
            raw = request.inputs["date"][0].data
            try:
                wanted = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise ProcessError(f"Invalid date {raw!r}: expected YYYY-MM-DD")
            scenes = [s for s in scenes if s.date.date() == wanted]
        if not scenes:
            raise ProcessError("No available scene fully covers this bbox (and date, if given)")

        response.update_status("Computing NDVI", 40)
        clip = scenes[0].ndvi(bbox)
        out = write_cog(clip, f"{self.workdir}/ndvi.tif")
        valid = clip.data[np.isfinite(clip.data)]
        stats = {
            "scene_id": clip.scene_id,
            "date": clip.date.date().isoformat(),
            "bbox_lonlat": list(bbox),
            "shape": list(clip.data.shape),
            "min": float(valid.min()), "mean": float(valid.mean()), "max": float(valid.max()),
            "valid_fraction": float(valid.size / clip.data.size),
            "radiometry": "radiance",
        }
        response.outputs["ndvi"].file = str(out)
        response.outputs["stats"].data = json.dumps(stats)
        return response
