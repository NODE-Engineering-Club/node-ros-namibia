from setuptools import setup

package_name = "sam2_vision"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (
            "share/" + package_name + "/config",
            ["config/sam2_params.yaml", "config/yolo_world_classes.yaml"],
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Njord",
    maintainer_email="njord@stud.ntnu.no",
    description=(
        "Autonomous prompt-guided SAM 2 zero-shot segmentation for the ASKET ASV"
    ),
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            # NOTE: entry-point names here (no ".py") are what
            # ros2 launch's `executable=` must reference — launching
            # "sam2_prompt_node.py" (with the suffix) is a different,
            # non-existent executable and will fail at runtime with
            # "executable not found", a mistake fixed in
            # sam2_perception_fusion.launch.py relative to the original
            # design note.
            "sam2_prompt_node = sam2_vision.sam2_prompt_node:main",
            "sam2_inference_node = sam2_vision.sam2_inference_node:main",
        ],
    },
)
