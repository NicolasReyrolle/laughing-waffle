# 3D Airspace KML Generator (Paragliding)

A command-line Python script that queries the **OpenAIP** API around a free-flight site and generates a **3D KML** file ready for visualization in **Google Earth**.

Airspace volumes are modeled as closed 3D polyhedra (floor, ceiling, and vertical side walls) so pilots can clearly assess vertical clearance between terrain relief and controlled or restricted airspaces.

---

## Features

- **Flexible Location Resolution:** Accepts a town/locality name (automatic geocoding via Nominatim/OpenStreetMap) or direct GPS coordinates (`lat,lon`).
- **Automatic Terrain Elevation (DEM):** Queries the free **Open-Meteo Elevation API** (Copernicus DEM 90 m) to convert **AGL / GND** floors and ceilings into true **AMSL** altitudes, automatically subdividing long polygon segments (`1.5 km`) so surfaces hug terrain relief.
- **Paragliding-Focused Visual Capping:** Filters out airspaces whose floor starts above the requested maximum altitude and visually caps higher ceilings to keep 3D volumes readable near the relief.
- **4-Color Classification & KML Folder Tree:** Groups airspaces into toggleable folders in Google Earth, distinguishing permanent (H24) TMAs from conditional/activatable TMAs (HX / NOTAM).
- **Ground Reference Markers:** Places a central marker (with terrain elevation) and draws the search radius ring on the ground.

---

## Color Code & KML Folder Structure

| KML Folder                    | Color  | Covered Airspaces                                                  | Free-Flight Rule                                       |
| :---------------------------- | :----- | :----------------------------------------------------------------- | :----------------------------------------------------- |
| **0. Repere Central & Rayon** | White  | Reference point and search perimeter                               | Ground visual reference                                |
| **1. Rouge**                  | Red    | CTR, Prohibited (P), Class A–D TMA/CTA (H24)                       | Prohibited / clearance required                        |
| **2. Orange**                 | Orange | Restricted (R), Danger (D), TRA, TSA, and **non-H24 / NOTAM TMAs** | Activatable — check AZBA / NOTAM / SUP AIP             |
| **3. Jaune**                  | Yellow | RMZ, TMZ, ATZ, aerobatic / parachuting zones                       | Vigilance or radio/transponder required                |
| **4. Bleu**                   | Blue   | Controlled **Class E** airspaces                                   | VFR permitted without clearance (IFR traffic possible) |

---

## Installation & Configuration

### 1. Prerequisites

- Python **3.10** or higher
- A free **OpenAIP** API key (available after creating an account at [openaip.net](https://www.openaip.net/))

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```
*(ou `pip install curl_cffi requests`)*

### 3. Configure Your API Key

Create a `.env` file in the project root directory:

```env
OPENAIP_API_KEY=your_openaip_api_key_here
```

---

## Usage

```bash
python generate_airspace_kml.py -l <LOCATION_OR_GPS> [OPTIONS]
```

### CLI Options

| Option              | Short | Default      | Description                                                                    |
| :------------------ | :---- | :----------- | :----------------------------------------------------------------------------- |
| `--location`        | `-l`  | _(required)_ | Town/locality name (e.g., `"Volmerange-les-Mines"`) or `"lat,lon"` coordinates |
| `--radius`          | `-r`  | `25`         | Search radius in kilometers around the center point                            |
| `--altitude`        | `-a`  | `3000`       | Max altitude (m AMSL) used to filter floors and cap 3D ceilings                |
| `--countries`       | `-c`  | _(auto)_     | ISO-2 country codes (e.g. `-c FR LU` for border areas). Defaults to location   |
| `--exclude-classes` | —     | _(none)_     | ICAO airspace classes to hide (e.g., `--exclude-classes E F G`)                |
| `--exclude-types`   | —     | _(none)_     | Airspace types to hide (e.g., `--exclude-types SPORT GLIDING`)                 |
| `--include-other`   | —     | `False`      | Include airspaces of type 'OTHER' (excluded by default)                        |
| `--no-dem`          | —     | `False`      | Disable DEM terrain elevation resolution (useful offline or for faster runs)   |
| `--output`          | `-o`  | _(auto)_     | Output `.kml` file path                                                        |
| `--env-file`        | —     | `.env`       | Path to the `.env` file containing `OPENAIP_API_KEY`                           |

---

## Usage Examples

### 1. Standard Generation Around a Town

Fetch all airspaces within 25 km of Volmerange-les-Mines, with a visual ceiling capped at 2500 m AMSL:

```bash
python generate_airspace_kml.py -l "Volmerange-les-Mines" -r 25 -a 2500
```

### 2. Declutter the Map by Hiding Class E

Keep only prohibited, activatable, and special-constraint airspaces:

```bash
python generate_airspace_kml.py -l "Volmerange-les-Mines" -r 25 -a 2500 --exclude-classes E
```

### 3. Using Exact Takeoff GPS Coordinates

Plan a cross-country flight from Le Treh (Vosges) over a 40 km radius up to 3200 m AMSL:

```bash
python generate_airspace_kml.py -l "47.9214,7.0128" -r 40 -a 3200 -o treh_cross_3d.kml
```

### 4. Fast Generation Without Terrain Elevation Lookup

```bash
python generate_airspace_kml.py -l "Annecy" -r 30 -a 3500 --no-dem
```

---

## Viewing in Google Earth

1. Open **Google Earth Pro** (desktop) or **Google Earth Web**.
2. Import the generated `.kml` file (`File > Open`).
3. Ensure the **Terrain / 3D Relief** layer is enabled in the bottom-left sidebar so you can inspect the actual clearance between mountain ridges and airspace floors.
4. Use the folder tree in the _Places_ panel on the left to toggle **Blue (Class E)** or **Orange (Activatable)** folders on or off with a single click, and click any airspace wall to view its class, remarks, and vertical limits.

---

## Disclaimer

This tool is intended strictly as a flight-preparation and spatial-awareness aid. Community-maintained OpenAIP datasets and DEM elevation interpolations may contain inaccuracies—always consult official aeronautical publications (**SIA / eAIP**, **ICAO charts**, **NOTAMs**, **SUP AIPs**, and local takeoff information boards) before flying.

The author assumes no responsibility for any incidents, damages, or legal consequences resulting from the use of this tool. Users must exercise their own judgment and comply with all applicable aviation regulations.

By using this tool, you acknowledge and accept that it is provided "as-is" without any warranties, express or implied, and that you bear full responsibility for any decisions made based on its output.

Always cross-check the generated airspace data with official sources before planning or conducting any flight operations.
