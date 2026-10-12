"""WPS processes: on-demand spectral indices from Bhoonidhi Resourcesat LISS-III scenes.

Two processes share one implementation (`IndexProcess`):
  ndvi            fixed index, output identifier `ndvi` (the headline process)
  spectral-index  the caller picks the index (ndvi, ndwi, mndwi, ndbi), output identifier `index`
"""
import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np
from pywps import BoundingBoxInput, ComplexOutput, Format, LiteralInput, Process
from pywps.app.exceptions import ProcessError
from pywps.inout.literaltypes import AnyValue

from app import live_fetch
from app.bhoonidhi_client import BhoonidhiError
from app.indices import INDICES
from app.preview import SCALES, render_preview
from app.radiometry import MODES, EsunNotConfigured
from app.scene import ATTRIBUTION, DATA_DIR, find_scenes, write_cog

GEOTIFF = Format("image/tiff; subtype=geotiff", extension=".tif")
JSON = Format("application/json", extension=".json")
PNG = Format("image/png", extension=".png")

MAX_PIXELS = int(os.environ.get("MAX_PIXELS", 16_000_000))  # keeps memory bounded (~100 MB per float32 band)


def _approx_pixels(bbox, resolution_m: float = 24.0) -> float:
    """Pixel count of a lon/lat bbox at LISS-III resolution (flat-earth estimate)."""
    minx, miny, maxx, maxy = bbox
    width_m = (maxx - minx) * 111_320 * math.cos(math.radians((miny + maxy) / 2))
    height_m = (maxy - miny) * 110_574
    return width_m * height_m / resolution_m ** 2


class NamedComplexOutput(ComplexOutput):
    """ComplexOutput whose file is named after its identifier (PyWPS would call it "input.tif").

    Outputs are handed to PyWPS as in-memory bytes, not as a path: PyWPS deletes the process working
    directory before it reads a file-backed output for `RawDataOutput`, so a path gives a 500 there.
    PyWPS writes the bytes to a file itself when a stored/reference response is needed, using this name.
    """

    def _build_file_name(self, href=""):
        # fixed name (no PyWPS "_xxxx" de-duplication suffix); the same bytes are written either way
        return os.path.join(self.workdir, f"{self.identifier}{self.extension}")


class IndexProcess(Process):
    """Clip the bbox from the best scene and compute a normalised-difference index.

    fixed_index: name from INDICES to bake into the process, or None to let the caller choose via an `index` input.
    """

    def __init__(self, identifier: str, title: str, fixed_index: str | None, output_id: str):
        self.fixed_index = fixed_index
        self.output_id = output_id
        inputs = [
            BoundingBoxInput(
                "bbox", "Area of interest",
                crss=["EPSG:4326"], min_occurs=1, max_occurs=1,
                abstract="Bounding box in WGS84. Axis order is lat,lon (EPSG:4326 per OGC): miny,minx,maxy,maxx.",
            ),
            LiteralInput(
                "date", "Acquisition date", data_type="string", min_occurs=0, max_occurs=1,
                allowed_values=[AnyValue()],  # WPS 1.0.0 schema requires an explicit AnyValue/AllowedValues
                abstract="ISO date (YYYY-MM-DD) of the scene to use. Defaults to the newest scene covering the bbox.",
            ),
            LiteralInput(
                "radiometry", "Radiometric quantity", data_type="string", min_occurs=0, max_occurs=1,
                allowed_values=list(MODES), default="radiance",
                abstract=(
                    "radiance (default): at-sensor radiance. Band-dependent solar irradiance is not removed, so "
                    "index values are biased and standard thresholds (for example NDWI > 0 for water) do not "
                    "strictly apply. toa: top-of-atmosphere reflectance (needs a cited per-band solar irradiance "
                    "table; refused with an explanatory error until one is configured). Neither removes "
                    "atmospheric effects."
                ),
            ),
            LiteralInput(
                "colour_scale", "Preview colour scale", data_type="string", min_occurs=0, max_occurs=1,
                allowed_values=list(SCALES), default="stretch",
                abstract=(
                    "stretch (default): colours span the 2nd to 98th percentile of this result, so only relative "
                    "contrast is shown. absolute: a fixed -1 to +1 scale, the same colours for the same values in "
                    "every request. Affects the preview image only, never the GeoTIFF values."
                ),
            ),
        ]
        if fixed_index is None:
            inputs.insert(1, LiteralInput(
                "index", "Spectral index", data_type="string", min_occurs=1, max_occurs=1,
                allowed_values=list(INDICES),
                abstract="; ".join(f"{i.name}: {i.formula} ({i.meaning})" for i in INDICES.values()),
            ))
            abstract = (
                "Computes a normalised-difference spectral index (" + ", ".join(INDICES) + ") for a bounding box from "
                "Resourcesat-2 LISS-III scenes published on Bhoonidhi (ISRO/NRSC). Bands: B2 green, B3 red, B4 NIR, "
                "B5 SWIR. Uses at-sensor radiance unless radiometry=toa is configured."
            )
            out_title = "Spectral index raster"
        else:
            idx = INDICES[fixed_index]
            abstract = (
                f"Computes the {idx.title} {idx.formula} for a bounding box from Resourcesat-2 LISS-III scenes "
                "published on Bhoonidhi (ISRO/NRSC). Uses at-sensor radiance unless radiometry=toa is configured "
                "(no atmospheric correction)."
            )
            out_title = f"{fixed_index.upper()} raster"
        outputs = [
            NamedComplexOutput(output_id, out_title, supported_formats=[GEOTIFF], as_reference=True,
                               abstract="Float32 Cloud Optimized GeoTIFF, NaN = no data, in the scene's UTM CRS."),
            NamedComplexOutput("preview", "Colour preview", supported_formats=[PNG], as_reference=True,
                               abstract="PNG in latitude/longitude for display on a web map: no-data is transparent and the "
                                        "colours are stretched between the 2nd and 98th percentile, so they show relative "
                                        "contrast only. Its map bounds and legend are in the stats output."),
            ComplexOutput("stats", "Index statistics", supported_formats=[JSON],
                          abstract="Index, scene id, acquisition date, and min/mean/max/valid fraction of the output."),
        ]
        super().__init__(
            self._handler,
            identifier=identifier,
            title=title,
            abstract=abstract,
            version="1.0.0",  # no keywords: WPS 1.0.0 schemas do not allow ows:Keywords in process descriptions
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

        name = self.fixed_index or request.inputs["index"][0].data
        if name not in INDICES:  # PyWPS already enforces allowed_values; this guards direct calls
            raise ProcessError(f"Unknown index {name!r}; available: {', '.join(INDICES)}")
        mode = request.inputs["radiometry"][0].data if "radiometry" in request.inputs else "radiance"
        colour_scale = request.inputs["colour_scale"][0].data if "colour_scale" in request.inputs else "stretch"

        wanted = None
        if "date" in request.inputs:
            raw = request.inputs["date"][0].data
            try:
                wanted = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise ProcessError(f"Invalid date {raw!r}: expected YYYY-MM-DD")

        response.update_status("Searching scenes", 10)
        scenes = [s for s in find_scenes(bbox, DATA_DIR) if wanted is None or s.date.date() == wanted]
        if not scenes and live_fetch.enabled():
            # Local mode only (needs a personal Bhoonidhi account in the environment): fetch the scene on demand.
            try:
                scenes = [live_fetch.ensure_scene(bbox, wanted, DATA_DIR, progress=response.update_status)]
            except BhoonidhiError as e:
                raise ProcessError(str(e))
        if not scenes:
            raise ProcessError("No available scene fully covers this bbox (and date, if given)")

        response.update_status(f"Computing {name.upper()}", 40)
        try:
            clip = scenes[0].index(bbox, name, mode)
        except EsunNotConfigured as e:
            raise ProcessError(str(e))
        out = write_cog(clip, f"{self.workdir}/{self.output_id}.tif")
        valid = clip.data[np.isfinite(clip.data)]
        if valid.size == 0:
            raise ProcessError("The selected scene has no valid pixels in this bbox")
        pv = render_preview(clip.data, clip.transform, clip.crs, name, colour_scale)
        stats = {
            "index": name,
            "formula": INDICES[name].formula,
            "scene_id": clip.scene_id,
            "date": clip.date.date().isoformat(),
            "bbox_lonlat": list(bbox),
            "shape": list(clip.data.shape),
            "min": float(valid.min()), "mean": float(valid.mean()), "max": float(valid.max()),
            "valid_fraction": float(valid.size / clip.data.size),
            "radiometry": mode,
            "attribution": ATTRIBUTION,
            "preview": {"bounds_south_west_north_east": list(pv.bounds), "vmin": pv.vmin, "vmax": pv.vmax,
                        "ramp": pv.ramp, "scale": colour_scale,
                        "note": ("fixed absolute scale -1..+1" if colour_scale == "absolute"
                                 else "colours are a per-request percentile stretch: relative contrast only")},
        }
        response.outputs[self.output_id].data = Path(out).read_bytes()
        response.outputs["preview"].data = pv.png
        response.outputs["stats"].data = json.dumps(stats)
        return response


class Ndvi(IndexProcess):
    def __init__(self):
        super().__init__("ndvi", "NDVI from Bhoonidhi LISS-III", fixed_index="ndvi", output_id="ndvi")


class SpectralIndex(IndexProcess):
    def __init__(self):
        super().__init__("spectral-index", "Spectral indices from Bhoonidhi LISS-III", fixed_index=None,
                         output_id="index")
