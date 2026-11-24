# Mission results

Light outputs of `scripts/process_mission.py`, one folder per mission day:
preview images, objects and seabed CSVs (lat, lon, depth), GPS track and
`report.json`. The heavy files (raw SonarView `.svlz`, black-box `.mcap`,
full `*_cloud.ply` / `*_seabed.ply` / `*_water.ply`) are in Azure, container
`asket-missions`, under `ASKET_02/sonarview/`, `ASKET_02/blackbox/` and
`ASKET_02/processed/<date>/`, and on the Jetson in `boat_data/`.

To refresh a day after re-processing:

    python3 scripts/process_mission.py --date YYYY-MM-DD
    cp boat_data/processed/YYYY-MM-DD/{*.png,*.csv,*.csv.gz,report.json,*_objects.ply} mission_results/YYYY-MM-DD/

Note: 2026-11-17 was processed before the angle-sign / mount-yaw fix (4b80c93)
and has no objects layer; its seabed result is unchanged by that fix.
