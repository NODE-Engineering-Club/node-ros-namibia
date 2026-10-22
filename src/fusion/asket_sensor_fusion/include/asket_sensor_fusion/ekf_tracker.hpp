#ifndef ASKET_SENSOR_FUSION__EKF_TRACKER_HPP_
#define ASKET_SENSOR_FUSION__EKF_TRACKER_HPP_

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

#include <Eigen/Dense>

namespace asket_sensor_fusion
{

// Constant-velocity Kalman tracker, 2D position+velocity in the map frame
// (state = [x, y, vx, vy]). This is the "3D EKF Tracking (Hungarian
// Association + velocity)" promised by the architecture (design note §1,
// §2.5) but never actually implemented in the provided geo_fusion_tracker_node
// code (it forwarded each per-frame detection untracked, with no track IDs,
// no velocity, no persistence). The predict/update equations below are a
// direct C++ transcription of fusion/fusion/geo_fusion_node.py's existing,
// already-tested Python Tracker — and independently re-derived and checked
// against hand-worked test vectors in verify_math.py before being
// transcribed, specifically to catch a transcription slip in either
// direction. Association is nearest-neighbour with a hard gate, sorted by
// distance (not full Hungarian/optimal assignment) — the same algorithm
// geo_fusion_node.py uses, kept deliberately simple and auditable rather
// than pulling in a Hungarian-solver dependency for a handful of objects
// per frame.
struct Track
{
  uint32_t id{0};
  Eigen::Vector4d x{Eigen::Vector4d::Zero()};  // [x, y, vx, vy], map frame, metres
  Eigen::Matrix4d P{Eigen::Matrix4d::Identity() * 4.0};
  std::string class_id{"unknown"};
  double confidence{0.0};
  double radius{0.0};
  int hits{1};
  int misses{0};
  bool confirmed{false};

  double speed() const {return std::hypot(x(2), x(3));}
};

struct Measurement
{
  double x{0.0};
  double y{0.0};
  std::string class_id{"unknown"};
  double confidence{0.0};
  double radius{0.0};
};

class EkfTracker
{
public:
  EkfTracker(
    double gate_m, double process_var, double meas_var,
    int confirm_hits, int max_misses)
  : gate2_(gate_m * gate_m),
    q_(process_var),
    R_(Eigen::Matrix2d::Identity() * meas_var),
    confirm_hits_(confirm_hits),
    max_misses_(max_misses)
  {
  }

  // Advances every existing track by dt (process model only — no
  // measurement yet). Call once per update() before association.
  void predict(double dt)
  {
    if (dt <= 0.0) {
      dt = 1e-3;
    }
    Eigen::Matrix4d F = Eigen::Matrix4d::Identity();
    F(0, 2) = dt;
    F(1, 3) = dt;

    const double dt2 = dt * dt;
    const double dt3 = dt2 * dt;
    const double dt4 = dt3 * dt;
    Eigen::Matrix4d Q = Eigen::Matrix4d::Zero();
    Q(0, 0) = q_ * dt4 / 4.0;
    Q(1, 1) = q_ * dt4 / 4.0;
    Q(0, 2) = q_ * dt3 / 2.0;
    Q(2, 0) = q_ * dt3 / 2.0;
    Q(1, 3) = q_ * dt3 / 2.0;
    Q(3, 1) = q_ * dt3 / 2.0;
    Q(2, 2) = q_ * dt2;
    Q(3, 3) = q_ * dt2;

    for (auto & tr : tracks_) {
      tr.x = F * tr.x;
      tr.P = F * tr.P * F.transpose() + Q;
    }
  }

  // Gated nearest-neighbour association + Kalman update for matched tracks,
  // new-track creation for unmatched measurements, and track pruning for
  // tracks that have missed too many frames in a row. Returns the CONFIRMED
  // tracks only (hits >= confirm_hits), matching geo_fusion_node.py's
  // contract: a track must be seen several times before Nav2 acts on it, so
  // one noisy spurious return does not trigger a bypass/keep-out.
  std::vector<Track> update(const std::vector<Measurement> & meas)
  {
    static const Eigen::Matrix<double, 2, 4> H = [] {
        Eigen::Matrix<double, 2, 4> h = Eigen::Matrix<double, 2, 4>::Zero();
        h(0, 0) = 1.0;
        h(1, 1) = 1.0;
        return h;
      } ();

    struct Pair
    {
      double d2;
      size_t mi;
      size_t ti;
    };
    std::vector<Pair> pairs;
    for (size_t mi = 0; mi < meas.size(); ++mi) {
      for (size_t ti = 0; ti < tracks_.size(); ++ti) {
        const double dx = meas[mi].x - tracks_[ti].x(0);
        const double dy = meas[mi].y - tracks_[ti].x(1);
        const double d2 = dx * dx + dy * dy;
        if (d2 <= gate2_) {
          pairs.push_back({d2, mi, ti});
        }
      }
    }
    std::sort(
      pairs.begin(), pairs.end(),
      [](const Pair & a, const Pair & b) {return a.d2 < b.d2;});

    std::vector<bool> meas_used(meas.size(), false);
    std::vector<bool> track_used(tracks_.size(), false);

    for (const auto & pr : pairs) {
      if (meas_used[pr.mi] || track_used[pr.ti]) {
        continue;
      }
      meas_used[pr.mi] = true;
      track_used[pr.ti] = true;

      Track & tr = tracks_[pr.ti];
      const Eigen::Vector2d z(meas[pr.mi].x, meas[pr.mi].y);
      const Eigen::Vector2d y = z - H * tr.x;
      const Eigen::Matrix2d S = H * tr.P * H.transpose() + R_;
      const Eigen::Matrix<double, 4, 2> K = tr.P * H.transpose() * S.inverse();
      tr.x = tr.x + K * y;
      tr.P = (Eigen::Matrix4d::Identity() - K * H) * tr.P;
      tr.hits += 1;
      tr.misses = 0;
      if (tr.hits >= confirm_hits_) {
        tr.confirmed = true;
      }
      tr.class_id = meas[pr.mi].class_id;
      tr.confidence = meas[pr.mi].confidence;
      tr.radius = meas[pr.mi].radius;
    }

    for (size_t ti = 0; ti < tracks_.size(); ++ti) {
      if (!track_used[ti]) {
        tracks_[ti].misses += 1;
      }
    }
    for (size_t mi = 0; mi < meas.size(); ++mi) {
      if (!meas_used[mi]) {
        Track tr;
        tr.id = next_id_++;
        tr.x = Eigen::Vector4d(meas[mi].x, meas[mi].y, 0.0, 0.0);
        tr.P = Eigen::Matrix4d::Identity();
        tr.P(0, 0) = tr.P(1, 1) = 0.5;
        tr.P(2, 2) = tr.P(3, 3) = 4.0;
        tr.class_id = meas[mi].class_id;
        tr.confidence = meas[mi].confidence;
        tr.radius = meas[mi].radius;
        tracks_.push_back(tr);
      }
    }

    tracks_.erase(
      std::remove_if(
        tracks_.begin(), tracks_.end(),
        [this](const Track & t) {return t.misses > max_misses_;}),
      tracks_.end());

    std::vector<Track> confirmed;
    for (const auto & t : tracks_) {
      if (t.confirmed) {
        confirmed.push_back(t);
      }
    }
    return confirmed;
  }

private:
  std::vector<Track> tracks_;
  uint32_t next_id_{0};
  double gate2_;
  double q_;
  Eigen::Matrix2d R_;
  int confirm_hits_;
  int max_misses_;
};

}  // namespace asket_sensor_fusion

#endif  // ASKET_SENSOR_FUSION__EKF_TRACKER_HPP_
