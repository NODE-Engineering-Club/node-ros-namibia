from setuptools import setup

package_name = "cloud_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", ["config/cloud_bridge_params.yaml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Njord",
    maintainer_email="njord@stud.ntnu.no",
    description="Outbound-only cloud telemetry relay for the ASKET ASV",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "cloud_telemetry_node = cloud_bridge.cloud_telemetry_node:main",
            "sonar_bridge_node = cloud_bridge.sonar_bridge_node:main",
        ],
    },
)
