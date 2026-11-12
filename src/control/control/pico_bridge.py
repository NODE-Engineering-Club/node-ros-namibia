"""
Pico Serial Bridge
Proprietaire unique du lien serie USB vers le Pico 2.

Fait 4 choses (un seul noeud car un seul owner de /dev/pico) :
  1. /control/effort (Twist) -> mixage skid-steer -> "L,R" serie
  2. Heartbeat : emet en CONTINU (timer). Commande fraiche -> "L,R",
     sinon -> "PING". Garde le lien "live" cote Pico (sinon il retombe
     seul en MANUAL en zone Ch8 HIGH apres 600 ms de silence).
  3. /pico/mode_request (String "AUTO"/"MANUAL") -> "MODE AUTO"/"MODE MANUAL"
     (le GUI de mission et Foxglove publient ce topic).
  4. Lit les lignes "STATE ..." du Pico (250 ms) -> /pico/status,
     et COMPTE celles qu'il rejette (voir plus bas : c'est le point).

Protocole firmware : pico-node_v5. USB CDC 115200, lignes '\\n'.
  "L,R" norm -1..1 -> 1500 + v*500 us  (norm si |v|<=1.5 -> on clamp a +-1)
  Moteurs appliques cote Pico SEULEMENT si arme + AUTONOMOUS + 2s apres relais.

Repris de node-ros-2026 (1606eb7, le detecteur de format) pour hw-alignment
Phase B (b), avec trois ajouts :

VERSION EXIGEE : ver=5 (asket_common.pico_link)
-----------------------------------------------
Le noeud de node-ros-2026 lisait la version annoncee par le Pico et se
contentait de la journaliser. Celui-ci ne transmet le MOUVEMENT -- consignes
moteur et "MODE AUTO" -- qu'a un Pico qui a annonce ver=5 (banniere "[VER]"
ou premier champ de chaque ligne STATE). Tant que ce n'est pas le cas, ou sur
toute autre version (en particulier un Pico reste en pico-node_v4, qui reste
arme si le SBUS se tait en AUTONOMOUS) :
  * une consigne moteur part comme "PING" : heartbeat oui, helices non ;
  * "MODE AUTO" n'est pas envoye ;
  * "MODE MANUAL" est toujours envoye : il ne fait que retirer de l'autorite.

ECRITURE NON BLOQUANTE
----------------------
write_timeout : si le Pico ne lit plus (USB sature), une ecriture qui ne passe
pas est abandonnee au lieu de bloquer le timer -- et avec lui la lecture du
STATE. Le Pico, lui, retombe seul au neutre sans consigne fraiche.

RECONNEXION
-----------
Si le port est absent au demarrage ou disparait (Pico rebranche, reset en
BOOTSEL, re-enumeration apres une chute de tension), le noeud retente de
l'ouvrir toutes les reopen_s secondes au lieu de rester sourd jusqu'au
redemarrage. Avec le lien udev /dev/pico, le nouveau ttyACMx est retrouve.

POURQUOI ON COMPTE LES LIGNES REJETEES
--------------------------------------
Le filtre ci-dessous a deja tout jete pendant des mois, en silence. Le firmware
flashe (v3) emettait "[STAT] ...", ce noeud cherchait "STATE...", aucune ligne
ne passait, /pico/status ne publiait jamais rien -- et comme le sens descendant
marchait (le bateau bougeait), rien n'avait l'air casse. Personne n'ecoutait ce
topic, donc personne n'a rien vu.

Un compteur et un log auraient transforme des mois d'enquete en une ligne de
journal au premier demarrage. C'est tout ce que fait ce code : il ne rend PAS
le noeud multi-format (un seul format, delibere -- deux formats acceptes veut
dire qu'un des deux n'est jamais teste). C'est un detecteur : le jour ou
quelqu'un flashe autre chose, il le dit tout de suite.
"""
import serial
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import String

from asket_common.pico_link import EXPECTED_FIRMWARE_VERSION, FirmwareGate

SERIAL_PORT = "/dev/pico"
BAUD_RATE   = 115200


class PicoBridge(Node):
    def __init__(self):
        super().__init__("pico_bridge")

        self.declare_parameter("port", SERIAL_PORT)
        self.declare_parameter("baud", BAUD_RATE)
        self.declare_parameter("send_hz", 20.0)     # >= 2 Hz requis (timeouts Pico 500/600 ms)
        self.declare_parameter("cmd_timeout", 0.3)  # s : au-dela -> PING (le Pico neutralise seul)
        self.declare_parameter("yaw_invert", 1)     # +1/-1 si le bateau tourne a l'envers
        self.declare_parameter("thr_invert", 1)     # +1/-1 si avant/arriere inverses
        self.declare_parameter("expected_fw_version", EXPECTED_FIRMWARE_VERSION)
        self.declare_parameter("write_timeout", 0.05)  # s : au-dela, l'ecriture est abandonnee
        self.declare_parameter("reopen_s", 2.0)        # s entre deux tentatives d'ouverture

        self._port = self.get_parameter("port").get_parameter_value().string_value
        self._baud = self.get_parameter("baud").get_parameter_value().integer_value
        self._cmd_timeout = self.get_parameter("cmd_timeout").get_parameter_value().double_value
        self._yaw_inv = self.get_parameter("yaw_invert").get_parameter_value().integer_value
        self._thr_inv = self.get_parameter("thr_invert").get_parameter_value().integer_value
        self._write_timeout = self.get_parameter("write_timeout").get_parameter_value().double_value
        self._reopen_s = self.get_parameter("reopen_s").get_parameter_value().double_value

        self._gate = FirmwareGate(
            self.get_parameter("expected_fw_version").get_parameter_value().integer_value)
        self._warned_version = False
        self._warned_dropped_writes = False
        self._dropped_writes = 0

        self._ser = None
        self._last_open_try = None
        self._open()

        # Derniere consigne recue + horodatage
        self._x = 0.0
        self._z = 0.0
        self._last_cmd = self.get_clock().now()
        self._rx = b""  # buffer de lecture serie

        # Detecteur de desaccord de format. Voir le docstring du module.
        self._lines_kept = 0
        self._lines_rejected = 0
        self._rejected_sample = ""
        self._warned_rejecting = False

        self.create_subscription(Twist, "/control/effort", self._on_effort, 10)
        self.create_subscription(String, "/pico/mode_request", self._on_mode, 10)
        self._status_pub = self.create_publisher(String, "/pico/status", 10)
        # Ce qui part reellement vers le Pico (consigne ou PING), pour le debug.
        self._debug_pub = self.create_publisher(String, "/pico_bridge/motor_cmd", 10)

        hz = self.get_parameter("send_hz").get_parameter_value().double_value
        self.create_timer(1.0 / hz, self._tick)

        self.get_logger().info(
            f"pico_bridge pret (heartbeat + modes + status), firmware exige : "
            f"v{self._gate.expected}")

    # --- Port serie ----------------------------------------------------------
    def _open(self):
        self._last_open_try = self.get_clock().now()
        try:
            self._ser = serial.Serial(
                self._port, self._baud, timeout=0, write_timeout=self._write_timeout)
            self._rx = b""
            # Un nouveau port peut etre un autre Pico (ou un Pico reflashe) :
            # la version doit etre annoncee a nouveau avant tout mouvement.
            self._gate = FirmwareGate(self._gate.expected)
            self._warned_version = False
            self.get_logger().info(f"Pico connecte sur {self._port} a {self._baud} baud")
        except serial.SerialException as e:
            if self._ser is None and self._last_open_try is not None:
                self.get_logger().warn(
                    f"Impossible d'ouvrir {self._port}: {e} — nouvel essai toutes les "
                    f"{self._reopen_s:.0f} s (publication seule, pas d'envoi série)",
                    throttle_duration_sec=30.0)
            self._ser = None

    def _ensure_open(self):
        if self._ser is not None and self._ser.is_open:
            return True
        elapsed = (self.get_clock().now() - self._last_open_try).nanoseconds / 1e9
        if elapsed >= self._reopen_s:
            self._open()
        return self._ser is not None

    def _lost_port(self, e):
        self.get_logger().warn(f"Port {self._port} perdu: {e} — reconnexion")
        try:
            self._ser.close()
        except Exception:
            pass
        self._ser = None

    # --- Entrees ROS ---------------------------------------------------------
    def _on_effort(self, msg):
        self._x = msg.linear.x
        self._z = msg.angular.z
        self._last_cmd = self.get_clock().now()

    def _on_mode(self, msg):
        line = self._gate.mode_line(msg.data)
        if line is not None:
            self._write(line)
            self.get_logger().info(f"-> {line} envoye au Pico")
            return
        word = msg.data.strip().upper().replace("MODE ", "")
        if word == "AUTO":
            self.get_logger().warn(
                f"MODE AUTO refuse : Pico {self._gate.describe()}. "
                f"Flasher firmware/pico-node_v{self._gate.expected}/.")
        else:
            self.get_logger().warn(f"mode_request inconnu : {msg.data!r}")

    # --- Boucle d'emission continue (coeur du heartbeat) ---------------------
    def _tick(self):
        if not self._ensure_open():
            return
        self._drain()  # lire le STATE du Pico + vider le buffer RX

        elapsed = (self.get_clock().now() - self._last_cmd).nanoseconds / 1e9
        if elapsed <= self._cmd_timeout:
            left, right = self._mix(self._x, self._z)
            line = self._gate.motor_line(left, right)
        else:
            # Pas de consigne fraiche : on garde le lien vivant, le Pico met neutre seul.
            line = "PING"
        self._write(line)
        self._debug_pub.publish(String(data=line))

    # --- Mixage skid-steer (ton design : left = x + z) -----------------------
    def _mix(self, x, z):
        x *= self._thr_inv
        z *= self._yaw_inv
        left  = x + z
        right = x - z
        m = max(abs(left), abs(right), 1.0)  # normalise seulement si depasse 1
        left, right = left / m, right / m
        return (max(-1.0, min(1.0, left)), max(-1.0, min(1.0, right)))

    # --- Lecture serie : republie les lignes STATE ---------------------------
    def _drain(self):
        if self._ser is None:
            return
        try:
            n = self._ser.in_waiting
            if n:
                self._rx += self._ser.read(n)
            while b"\n" in self._rx:
                raw, self._rx = self._rx.split(b"\n", 1)
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                if self._gate.observe(line):
                    self._on_version_change()
                if line.startswith("STATE"):
                    self._lines_kept += 1
                    self._status_pub.publish(String(data=line))
                    continue
                if line.startswith("[VER] "):
                    continue
                # Tout le reste : [ACK], [NAK], [E-STOP], "Invalid cmd:", bruit
                # serie. Compte, pas jete en silence.
                self._lines_rejected += 1
                if not self._rejected_sample:
                    self._rejected_sample = line[:80]
            self._check_format()
            self._check_version()
        except (serial.SerialException, OSError) as e:
            self._lost_port(e)

    # --- Detection de desaccord de version et de format ----------------------
    def _on_version_change(self):
        if self._gate.ok:
            self.get_logger().info(f"Pico firmware : {self._gate.describe()}")
            self._warned_version = False
        else:
            self._warned_version = True
            self.get_logger().error(
                f"Pico firmware : {self._gate.describe()}. Consignes moteur et "
                f"MODE AUTO NON transmis (PING seulement). Flasher "
                f"firmware/pico-node_v{self._gate.expected}/ (firmware/README.md).")

    def _check_version(self):
        """Crie une fois si le Pico parle sans jamais annoncer sa version."""
        if self._warned_version or self._gate.reported is not None:
            return
        if self._lines_kept + self._lines_rejected < 20:
            return
        self._warned_version = True
        self.get_logger().error(
            f"Le Pico emet ({self._lines_kept + self._lines_rejected} lignes) sans "
            f"annoncer de version : aucun mouvement ne sera transmis. Firmware "
            f"attendu : pico-node_v{self._gate.expected}.")

    def _check_format(self):
        """Crie une fois si le Pico parle et qu'on ne comprend rien.

        Le seuil (20 lignes rejetees, zero gardee) correspond a environ cinq
        secondes de STATE a 4 Hz : assez pour ne pas crier sur un fragment de
        ligne au demarrage, assez peu pour que ce soit dit avant la mise a
        l'eau.
        """
        if self._warned_rejecting or self._lines_kept:
            return
        if self._lines_rejected < 20:
            return
        self._warned_rejecting = True
        self.get_logger().error(
            f"Le Pico emet ({self._lines_rejected} lignes) mais AUCUNE ne "
            f"commence par 'STATE' : /pico/status ne publiera rien. "
            f"Exemple : {self._rejected_sample!r}. "
            f"Firmware attendu : pico-node_v{self._gate.expected}. Un firmware "
            f"plus ancien (v3) emet '[STAT] Mode:...' et n'est pas supporte."
        )

    # --- Ecriture serie ------------------------------------------------------
    def _write(self, s):
        if self._ser is None or not self._ser.is_open:
            return
        try:
            self._ser.write((s + "\n").encode())
        except serial.SerialTimeoutException:
            # Le Pico ne lit plus : on abandonne cette ligne plutot que de
            # bloquer. Il retombe seul au neutre sans consigne fraiche.
            self._dropped_writes += 1
            if not self._warned_dropped_writes:
                self._warned_dropped_writes = True
                self.get_logger().warn(
                    "Ecriture vers le Pico abandonnee (le Pico ne lit plus le port)")
        except (serial.SerialException, OSError) as e:
            self._lost_port(e)

    def destroy_node(self):
        self._write("0,0")  # neutre a la sortie
        if self._ser and self._ser.is_open:
            self._ser.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = PicoBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
