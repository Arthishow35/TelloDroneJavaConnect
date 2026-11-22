import os
import re
import socket
import threading
import time

import cv2

TELLO_ADDRESS = ("192.168.10.1", 8889)
VIDEO_URL = "udp://0.0.0.0:11111"
CAPTURE_FOLDER = "grabbedImages"
PATHS_FOLDER = "paths"
CAPTURE_INTERVAL = 5
DEFAULT_TIMEOUT = 7
MANEUVER_TIMEOUT = 30
KEEP_ALIVE_INTERVAL = 5


def saisir_nombre(message, mini, maxi):
    while True:
        brut = input(message).strip().replace(",", ".")
        try:
            valeur = float(brut)
        except ValueError:
            print("   -> entre un nombre")
            continue
        if valeur < mini or valeur > maxi:
            print(f"   -> valeur entre {mini} et {maxi}")
            continue
        return valeur


def decrire(commande):
    parties = commande.split()
    action = parties[0]
    if action in ("forward", "back", "left", "right", "up", "down"):
        nom = {
            "forward": "Avancer", "back": "Reculer", "left": "Aller à gauche",
            "right": "Aller à droite", "up": "Monter", "down": "Descendre",
        }[action]
        return f"{nom} de {int(parties[1]) / 100:.2f} m"
    if action == "cw":
        return f"Pivoter de {parties[1]} degrés (horaire)"
    if action == "ccw":
        return f"Pivoter de {parties[1]} degrés (anti-horaire)"
    if action == "takeoff":
        return "Décollage"
    if action == "land":
        return "Atterrissage"
    if action == "speed":
        return f"Vitesse {parties[1]} cm/s"
    if action == "wait":
        return f"Attendre {parties[1]} s"
    return commande


class Drone:
    def __init__(self, simulation=False):
        self.simulation = simulation
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 9000))
        self.verrou = threading.Lock()

    def envoyer(self, commande, timeout=DEFAULT_TIMEOUT, essais=3):
        if self.simulation:
            print(f"   [simulation] {commande}")
            return "ok"
        with self.verrou:
            for _ in range(essais):
                try:
                    self.sock.sendto(commande.encode("utf-8"), TELLO_ADDRESS)
                    self.sock.settimeout(timeout)
                    reponse, _ = self.sock.recvfrom(1024)
                    texte = reponse.decode("utf-8", errors="ignore").strip().strip("\x00")
                    if texte.lower() == "ok":
                        print(f"   {commande} -> ok")
                    else:
                        print(f"   {commande} -> {texte}")
                    return texte
                except socket.timeout:
                    if essais > 1:
                        print(f"   {commande} -> pas de réponse, nouvel essai...")
            print(f"   {commande} -> ÉCHEC")
            return None

    def connecter(self):
        if not self.envoyer("command"):
            return False
        self.envoyer("battery?", essais=1)
        self.envoyer("speed?", essais=1)
        return True

    def executer(self, commande):
        action = commande.split()[0]
        reponse = self.envoyer(commande, timeout=MANEUVER_TIMEOUT if action in ("takeoff", "land") else DEFAULT_TIMEOUT)
        time.sleep(0.2)
        return reponse == "ok"

    def fermer(self):
        self.sock.close()


class GestionnaireVideo:
    def __init__(self):
        self.capture = None
        self.capture_active = threading.Event()
        self.arret = threading.Event()
        self.fenetre_masquee = False
        self.index = 0
        self.thread = threading.Thread(target=self._boucle, daemon=True)

    def demarrer(self):
        os.makedirs(CAPTURE_FOLDER, exist_ok=True)
        existantes = [n for n in os.listdir(CAPTURE_FOLDER) if re.fullmatch(r"capture_\d{4}\.jpg", n)]
        self.index = len(existantes)
        self.thread.start()

    def _boucle(self):
        cap = None
        for _ in range(5):
            cap = cv2.VideoCapture(VIDEO_URL, cv2.CAP_FFMPEG)
            if cap.isOpened():
                break
            time.sleep(2)
        if not cap or not cap.isOpened():
            print("[Vue drone] OpenCV n'a pas pu ouvrir le flux UDP (programme sans vue, mais commandes OK).")
            cap = None
            self.capture = None
            self.arret.wait()
            return
        self.capture = cap
        prochaine_capture = time.time() + CAPTURE_INTERVAL
        print("[Vue drone] fenêtre live ouverte ; captures toutes les " + str(CAPTURE_INTERVAL) + " s dans " + CAPTURE_FOLDER + "/")
        while not self.arret.is_set():
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue
            if not self.fenetre_masquee:
                cv2.imshow("Vue drone (q pour masquer)", frame)
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    cv2.destroyAllWindows()
                    self.fenetre_masquee = True
                    print("[Vue drone] fenêtre masquée (captures continuent).")
            if self.capture_active.is_set() and time.time() >= prochaine_capture:
                self.index += 1
                chemin = os.path.join(CAPTURE_FOLDER, f"capture_{self.index:04d}.jpg")
                cv2.imwrite(chemin, frame)
                prochaine_capture = time.time() + CAPTURE_INTERVAL
        cap.release()

    def arreter(self):
        self.arret.set()
        cv2.destroyAllWindows()
        time.sleep(0.2)


def boucle_keep_alive(drone, en_vol, arret):
    while not arret.wait(KEEP_ALIVE_INTERVAL):
        if en_vol.is_set():
            drone.envoyer("speed?", timeout=3, essais=1)


def construire_liste():
    print("\n=== Ajout de commandes (0 pour terminer) ===")
    commandes = []
    while True:
        print()
        print("1 - Avancer de X m         7 - Pivoter de X degrés (horaire)")
        print("2 - Reculer de X m         8 - Pivoter de X degrés (anti-horaire)")
        print("3 - Aller à gauche de X m  9 - Décollage")
        print("4 - Aller à droite de X m 10 - Atterrissage")
        print("5 - Monter de X m         11 - Régler la vitesse (10-100 cm/s)")
        print("6 - Descendre de X m      12 - Attendre X secondes")
        print("0 - Terminer la liste de commandes")
        choix = input("Choix : ").strip()
        if choix == "0":
            return commandes
        if choix in ("1", "2", "3", "4"):
            nom = {"1": ("forward", "Avancer"), "2": ("back", "Reculer"), "3": ("left", "Aller à gauche"), "4": ("right", "Aller à droite")}[choix]
            metres = saisir_nombre(f"   distance en m (0.2 à 5) pour '{nom[1]}' : ", 0.2, 5.0)
            commandes.append(f"{nom[0]} {int(metres * 100)}")
        elif choix == "5":
            metres = saisir_nombre("   hauteur en m (0.2 à 5) : ", 0.2, 5.0)
            commandes.append(f"up {int(metres * 100)}")
        elif choix == "6":
            metres = saisir_nombre("   hauteur en m (0.2 à 5) : ", 0.2, 5.0)
            commandes.append(f"down {int(metres * 100)}")
        elif choix in ("7", "8"):
            degres = int(saisir_nombre("   angle en degrés (1 à 360) : ", 1, 360))
            commandes.append(f"{'cw' if choix == '7' else 'ccw'} {degres}")
        elif choix == "9":
            commandes.append("takeoff")
        elif choix == "10":
            commandes.append("land")
        elif choix == "11":
            vitesse = int(saisir_nombre("   vitesse en cm/s (10 à 100) : ", 10, 100))
            commandes.append(f"speed {vitesse}")
        elif choix == "12":
            duree = saisir_nombre("   durée en s (0 à 60) : ", 0, 60)
            commandes.append(f"wait {duree:g}")
        else:
            print("   -> choix invalide")
            continue
        print(f"   -> ajouté : {decrire(commandes[-1])}")


def sauvegarder_chemin(commandes):
    os.makedirs(PATHS_FOLDER, exist_ok=True)
    nom = input("   nom du chemin : ").strip()
    nom = re.sub(r"[^A-Za-z0-9 _\-.]", "", nom)
    if not nom:
        print("   -> nom invalide, chemin non sauvegardé")
        return False
    chemin = os.path.join(PATHS_FOLDER, nom + ".txt")
    if os.path.exists(chemin):
        reponse = input(f"   '{nom}' existe déjà, écraser ? (o/n) : ").strip().lower()
        if reponse != "o":
            print("   -> sauvegarde annulée")
            return False
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write("\n".join(commandes) + "\n")
    print(f"   -> chemin sauvegardé dans {chemin}")
    return True


def charger_chemin():
    os.makedirs(PATHS_FOLDER, exist_ok=True)
    fichiers = sorted(f for f in os.listdir(PATHS_FOLDER) if f.endswith(".txt"))
    if not fichiers:
        print("   -> aucun chemin sauvegardé")
        return None
    print()
    for i, nom in enumerate(fichiers, 1):
        print(f"{i} - {nom[:-4]}")
    choix = saisir_nombre("   numéro du chemin : ", 1, len(fichiers))
    avec_fichier = fichiers[int(choix) - 1]
    chemin = os.path.join(PATHS_FOLDER, avec_fichier)
    with open(chemin, encoding="utf-8") as fichier:
        commandes = [ligne.strip() for ligne in fichier if ligne.strip()]
    print(f"\n=== Chemin '{avec_fichier[:-4]}' ({len(commandes)} commande(s)) ===")
    for i, commande in enumerate(commandes, 1):
        print(f"[{i}/{len(commandes)}] {decrire(commande)}")
    return commandes


def executer(drone, commandes):
    if not commandes:
        print("   -> liste vide")
        return
    en_vol = drone.en_vol
    print(f"\n=== Début de l'exécution ({len(commandes)} commande(s)) ===")
    debut = time.time()
    echecs = 0
    en_vol.set()
    for i, commande in enumerate(commandes, 1):
        print(f"[{i}/{len(commandes)}] {decrire(commande)}")
        if commande.split()[0] == "wait":
            duree = float(commande.split()[1])
            time.sleep(duree)
        else:
            if not drone.executer(commande):
                echecs += 1
                echec_nom = commande.split()[0]
                if echec_nom == "takeoff":
                    en_vol.clear()
            elif commande == "land":
                en_vol.clear()
        time.sleep(0.2)
    duree = time.time() - debut
    bilan = "aucun échec" if echecs == 0 else f"{echecs} en échec"
    print(f"=== Fin de l'exécution en {duree:.0f} s ({len(commandes)} commande(s), {bilan}) ===")


def main():
    print("=== Tello Drone - Pilotage en terminal ===")
    print("Assure-toi d'être connecté au Wi-Fi du drone (192.168.10.1).")
    drone = Drone(False)
    if not drone.connecter():
        print("Impossible de joindre le drone : les commandes seront simulées (aucun envoi).")
        drone.fermer()
        drone = Drone(simulation=True)
        video = None
    else:
        video = GestionnaireVideo()
        if not drone.envoyer("streamon"):
            print("[Vue drone] streamon refusé par le drone : pas de vue, mais commandes possibles.")
            video = None
        else:
            video.demarrer()
            video.capture_active.set()
    en_vol = threading.Event()
    drone.en_vol = en_vol
    arret_keep_alive = threading.Event()
    if not drone.simulation:
        threading.Thread(target=boucle_keep_alive, args=(drone, en_vol, arret_keep_alive), daemon=True).start()
    try:
        while True:
            print()
            print("========== MENU PRINCIPAL ==========")
            print("1 - Entrer des commandes")
            print("2 - Créer un nouveau chemin")
            print("3 - Charger un chemin")
            print("0 - Quitter")
            choix = input("Choix : ").strip()
            if choix == "1":
                commandes = construire_liste()
                if commandes:
                    print()
                    print("1 - Exécuter immédiatement")
                    print("2 - Sauvegarder comme chemin")
                    print("3 - Retour au menu principal")
                    action = input("Choix : ").strip()
                    if action == "1":
                        executer(drone, commandes)
                    elif action == "2":
                        sauvegarder_chemin(commandes)
            elif choix == "2":
                commandes = construire_liste()
                if commandes:
                    sauvegarder_chemin(commandes)
            elif choix == "3":
                commandes = charger_chemin()
                if commandes:
                    action = input("\n1 - Lancer ce chemin\n0 - Retour\nChoix : ").strip()
                    if action == "1":
                        executer(drone, commandes)
            elif choix == "0":
                break
            else:
                print("   -> choix invalide")
    finally:
        arret_keep_alive.set()
        if not drone.simulation:
            drone.envoyer("land", timeout=MANEUVER_TIMEOUT, essais=1)
            drone.envoyer("streamoff", essais=1)
        if video is not None:
            video.arreter()
        drone.fermer()
        print("Programme terminé.")


if __name__ == "__main__":
    main()
