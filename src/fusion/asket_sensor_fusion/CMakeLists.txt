cmake_minimum_required(VERSION 3.8)
project(asket_sensor_fusion)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(vision_msgs REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(tf2_sensor_msgs REQUIRED)
find_package(tf2_geometry_msgs REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(message_filters REQUIRED)
find_package(PCL REQUIRED COMPONENTS common io sample_consensus segmentation filters)
find_package(pcl_conversions REQUIRED)
find_package(OpenCV REQUIRED)
find_package(Eigen3 REQUIRED)

include_directories(include)

add_executable(lidar_water_filter_node src/lidar_water_filter_node.cpp)
ament_target_dependencies(
  lidar_water_filter_node
  rclcpp
  sensor_msgs
  tf2
  tf2_ros
  tf2_sensor_msgs
  pcl_conversions
)
target_link_libraries(lidar_water_filter_node ${PCL_LIBRARIES})
target_include_directories(lidar_water_filter_node PRIVATE ${PCL_INCLUDE_DIRS})

add_executable(sensor_fusion_obstacle_node src/sensor_fusion_obstacle_node.cpp)
ament_target_dependencies(
  sensor_fusion_obstacle_node
  rclcpp
  sensor_msgs
  vision_msgs
  tf2
  tf2_ros
  tf2_sensor_msgs
  cv_bridge
  message_filters
  pcl_conversions
)
target_link_libraries(sensor_fusion_obstacle_node ${PCL_LIBRARIES} ${OpenCV_LIBRARIES})
target_include_directories(
  sensor_fusion_obstacle_node PRIVATE ${PCL_INCLUDE_DIRS} ${OpenCV_INCLUDE_DIRS})

add_executable(geo_fusion_tracker_node src/geo_fusion_tracker_node.cpp)
ament_target_dependencies(
  geo_fusion_tracker_node
  rclcpp
  sensor_msgs
  geometry_msgs
  vision_msgs
  tf2_ros
  tf2_geometry_msgs
)
target_link_libraries(geo_fusion_tracker_node Eigen3::Eigen)

install(
  TARGETS lidar_water_filter_node sensor_fusion_obstacle_node geo_fusion_tracker_node
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY include/
  DESTINATION include
)

install(
  DIRECTORY config
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
