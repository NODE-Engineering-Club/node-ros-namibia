# --- Mission GUI frontend (src/asket_gui), built once, copied into prod ------
#
# The React frontend must be built BEFORE colcon: gui_backend/setup.py collects
# gui_backend/gui_backend/static/ while it runs, and an empty directory there
# installs a backend that answers 503. Node is only needed for this step, so it
# lives in its own stage and never reaches the runtime image.
#
# --platform=$BUILDPLATFORM: the output is plain HTML/JS/CSS, identical on every
# architecture, so it is built natively rather than under QEMU arm64 emulation.
# Ported from node-ros-2026 1606eb7 (where Node was installed in the dev stage).
FROM --platform=$BUILDPLATFORM docker.io/library/node:20-bookworm-slim AS gui-frontend
WORKDIR /ws/src/asket_gui
COPY src/asket_gui/package.json src/asket_gui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/asket_gui/ ./
# vite.config.js writes to ../gui_backend/gui_backend/static
RUN npm run build && test -f /ws/src/gui_backend/gui_backend/static/index.html

FROM docker.io/ros:jazzy-ros-base AS base

RUN apt-get update && apt-get install -y \
    libgtk-3-0 \
    libglib2.0-0 \
    libgl1 \
    pkg-config \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

RUN apt-get update && apt-get install -y --no-install-recommends \
    # MAVROS2 + MAVLink
    ros-jazzy-mavros \
    ros-jazzy-mavros-extras \
    ros-jazzy-mavros-msgs \
    ros-jazzy-geographic-msgs \
    ros-jazzy-tf2-ros \
    ros-jazzy-tf2-geometry-msgs \
    # Sensors
    v4l-utils \
    # pyserial: bms_reader and pico_bridge both `import serial`, and nothing
    # installed it, so both exited at import in this image.
    python3-serial \
    ros-jazzy-cv-bridge \
    # Vision / Perception
    libgl1 \
    ros-jazzy-vision-msgs \
    # Localization
    ros-jazzy-robot-localization \
    # Navigation
    ros-jazzy-nav2-bringup \
    # Control / Mission
    ros-jazzy-nav2-msgs \
    ros-jazzy-twist-mux \
    # Custom message generation
    ros-jazzy-rosidl-default-generators \
    # Telemetry bridge
    # ros-jazzy-rosbridge-suite \
    # ros-jazzy-web-video-server \
    ros-jazzy-foxglove-bridge \
    ros-jazzy-ros-gz-bridge \
    ros-jazzy-ros-gz-sim \
    # Camera calibration
    ros-jazzy-camera-calibration-parsers \
    ros-jazzy-camera-info-manager \
    ros-jazzy-camera-info-manager-py \
    ros-jazzy-launch-testing-ament-cmake \
    ros-jazzy-camera-calibration \
    # LiDAR-camera extrinsic calibration
    ros-jazzy-laser-geometry \
    python3-scipy \
    python3-sklearn \
    python3-pip \
    git \
    # asket_sensor_fusion (Kelp SAM2/fusion pipeline): PCL-based nodes.
    # These are plain CPU C++ deps with no Jetson/CUDA requirement, so they
    # live in the shared base stage and build on every platform CI already
    # targets — only the TensorRT/CUDA runtime for sam2_vision is isolated
    # to the "prod-jetson" stage below.
    libpcl-dev \
    libeigen3-dev \
    ros-jazzy-pcl-conversions \
    ros-jazzy-tf2-sensor-msgs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

RUN usermod -aG dialout root

# --- Dev target: workspace is bind-mounted, used by devcontainer ---------------
FROM base AS dev
RUN echo "source /opt/ros/jazzy/setup.bash" >> /etc/bash.bashrc && \
    echo '[ -f /workspace/install/setup.bash ] && source /workspace/install/setup.bash' >> /etc/bash.bashrc
WORKDIR /workspace

# --- Prod target: code baked in, built with colcon ----------------------------
FROM base AS prod

# GeographicLib datasets required by MAVROS GPS plugins
RUN wget -q https://raw.githubusercontent.com/mavlink/mavros/ros2/mavros/scripts/install_geographiclib_datasets.sh \
    && chmod +x install_geographiclib_datasets.sh \
    && ./install_geographiclib_datasets.sh \
    && rm install_geographiclib_datasets.sh

# pip-only deps (no rosdep keys exist for these)
RUN pip install --break-system-packages --no-cache-dir \
     "numpy<2" \
     onnxruntime \
     opencv-python \
     transforms3d \
     simple-pid \
     websockets \
     fastapi \
     "uvicorn[standard]"

# Requires the src/sllidar_ros2 submodule to be checked out on the host
# (git submodule update --init --recursive) before building — COPY only
# copies what's already on disk in the build context.
COPY src/ /ros2_ws/src/

# The built mission GUI frontend, before colcon so gui_backend installs it.
# prod-jetson inherits it from this stage.
COPY --from=gui-frontend /ws/src/gui_backend/gui_backend/static/ /ros2_ws/src/gui_backend/gui_backend/static/

RUN . /opt/ros/jazzy/setup.sh && \
    colcon build \
        --base-paths /ros2_ws/src \
        --install-base /opt/njord \
        --cmake-args -DCMAKE_BUILD_TYPE=Release

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Everything the stack writes and must keep lives under /data: recorded
# missions (/data/missions), the pre-flight history (/data/system_test.jsonl),
# map tiles (/data/maps) and the Setup page's file. The setup file defaulted to
# ~/.ros/asket_setup.yaml — inside the container, gone with it on every
# restart. Mount a host directory on /data to keep all of it
# (deploy/jetson/asket.service does); without one, nothing survives a restart.
ENV ASKET_SETUP_FILE=/data/setup/asket_setup.yaml
RUN mkdir -p /data/missions /data/maps /data/setup

ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "bringup", "njord.launch.py"]

# --- Jetson-only target: SAM2/TensorRT runtime for the Kelp perception -------
#
# NOT built by .github/workflows/build.yaml (which only ever builds
# `target: prod`, on a generic QEMU-emulated arm64 runner with no NVIDIA apt
# repo and no GPU). Building this stage is a manual, on/for-the-Jetson step:
#
#   docker build --target prod-jetson -t asket:prod-jetson .
#
# This FROM line assumes an x86_64/arm64 build host where pip can resolve
# CUDA-enabled tensorrt/pycuda wheels (e.g. an actual Jetson with NVIDIA's
# JetPack apt repo already configured, or NVIDIA's L4T/CUDA base image).
# `docker.io/ros:jazzy-ros-base` alone does NOT provide that repo — if
# `apt-get install libnvinfer10 ...` 404s on your build host, you are not
# building this stage on top of an NVIDIA-repo-enabled base and need to
# either add NVIDIA's apt source first or switch this FROM to an L4T image
# (e.g. a dustynv/ros:jazzy-* or nvcr.io/nvidia/l4t-* base) — not verified
# against a real Jetson in this change; see KELP_CAPABILITIES.md §0.5 for
# the Jetson-vs-Pi platform decision this depends on.
FROM prod AS prod-jetson

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnvinfer10 \
    libnvinfer-plugin10 \
    libnvonnxparsers10 \
    libnvinfer-dev \
    python3-libnvinfer \
    cuda-nvtx-12-6 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Separate pip layer from the "prod" stage's CPU-only installs: pycuda's
# build needs nvcc/the CUDA toolkit present (from the apt layer above), and
# the sam2 package is a git checkout, not a PyPI release.
RUN pip install --break-system-packages --no-cache-dir \
     pycuda==2024.1 \
     ultralytics>=8.3.0 \
     git+https://github.com/facebookresearch/sam2.git

CMD ["ros2", "launch", "bringup", "sam2_perception_fusion.launch.py"]
