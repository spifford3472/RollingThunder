RollingThunder Repeater Upload Folder
=====================================

Purpose
-------
This folder is for local rt-controller repeater database maintenance files.
It is used to stage CSV files that are imported into the local SQLite repeater
maintenance database:

  /opt/rollingthunder/data/vhf/repeaters.sqlite3

This is maintenance tooling only. The RollingThunder UI does not read this
folder or this database directly. This phase does not control the radio,
does not control a scanner, does not create runtime Redis models, and does
not change any UI page.

Original CSV format
-------------------
The original repeater CSV is expected to be named:

  RollingThunder-Repeaters.csv

The original CSV columns are:

  Channel_Name
  Channel_Type
  Rx_Frequency
  Tx_Frequency
  Bandwidth_kHz
  State
  RX_Tone
  TX_Tone
  Latitude
  Longitude
  Special

The original CSV does not include an Action column. Because of that, the
maintenance tool will not silently assume Add. To import the original CSV,
you must explicitly use one of these options:

  --default-action Add

or:

  --initial-load

Maintenance CSV format
----------------------
A maintenance CSV uses the same columns as the original CSV and adds one final
column:

  Action

Valid Action values are:

  Add
  Update
  Remove

Action meanings
---------------
Add:
  Adds a new repeater row if a matching repeater does not already exist.

Update:
  Updates an existing repeater row that matches the maintenance key.

Remove:
  Removes an existing repeater row that matches the maintenance key.

The current match key is:

  Channel_Name + Rx_Frequency + Tx_Frequency + State

If Action exists in a CSV, the tool uses it. Blank or invalid Action values are
counted as errors and skipped.

Initial load
------------
Place the original CSV here:

  /opt/rollingthunder/upload/RollingThunder-Repeaters.csv

Run an initial dry-run validation first:

  python3 /opt/rollingthunder/upload/manage_repeaters_csv.py \
    --db /opt/rollingthunder/data/vhf/repeaters.sqlite3 \
    --import /opt/rollingthunder/upload/RollingThunder-Repeaters.csv \
    --default-action Add \
    --dry-run

Then run the actual import:

  python3 /opt/rollingthunder/upload/manage_repeaters_csv.py \
    --db /opt/rollingthunder/data/vhf/repeaters.sqlite3 \
    --import /opt/rollingthunder/upload/RollingThunder-Repeaters.csv \
    --default-action Add

You may use --initial-load instead of --default-action Add for the original CSV:

  python3 /opt/rollingthunder/upload/manage_repeaters_csv.py \
    --db /opt/rollingthunder/data/vhf/repeaters.sqlite3 \
    --import /opt/rollingthunder/upload/RollingThunder-Repeaters.csv \
    --initial-load

Dry-run validation
------------------
Use --dry-run to validate a CSV and print a summary without adding, updating,
or removing repeater rows:

  python3 /opt/rollingthunder/upload/manage_repeaters_csv.py \
    --db /opt/rollingthunder/data/vhf/repeaters.sqlite3 \
    --import /opt/rollingthunder/upload/RollingThunder-Repeaters.csv \
    --default-action Add \
    --dry-run

Export a maintenance CSV
------------------------
Export the current SQLite repeater data to a maintenance template:

  python3 /opt/rollingthunder/upload/manage_repeaters_csv.py \
    --db /opt/rollingthunder/data/vhf/repeaters.sqlite3 \
    --export /opt/rollingthunder/upload/repeaters-export.csv

The exported file includes an Action column, but Action is blank by default.
The exported CSV is intended as a maintenance template. Fill Action with Update
or Remove before re-importing rows you want to change or delete.

SkyWarn marking rule
--------------------
The tool sets is_skywarn=1 when any of these fields contain the text skywarn,
case-insensitive:

  Special
  Channel_Name
  Channel_Type

Examples that mark SkyWarn include:

  SKYWARN
  SkyWarn
  Skywarn
  skywarn

Bucket rule
-----------
The tool calculates quarter-degree location buckets:

  lat_bucket_025 = floor(latitude / 0.25)
  lon_bucket_025 = floor(longitude / 0.25)

Python math.floor is used so negative longitudes bucket correctly.

Validation
----------
The tool validates:

  Rx_Frequency is numeric
  Tx_Frequency is numeric
  Bandwidth_kHz is numeric when present
  Latitude is numeric and between -90 and 90
  Longitude is numeric and between -180 and 180
  Action is Add, Update, or Remove when Action exists

Blank RX_Tone, TX_Tone, and Special fields are allowed.
Extra CSV columns are tolerated and preserved in raw_json.

Architecture boundary
---------------------
This folder and tool are controller/local maintenance only.

The UI must not:

  read this upload folder
  import CSV
  read the SQLite database
  calculate repeater distance
  filter repeaters
  infer VHF/UHF radio availability

This phase does not:

  create a VHF/UHF page
  create a topbar VHF indicator
  create Redis runtime models
  control the IC-2730A
  implement scanning
  implement Side A or Side B logic
  implement C/D memory group flipping
  implement SkyWarn runtime behavior