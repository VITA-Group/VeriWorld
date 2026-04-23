"""
End-to-end verification: launch UE, run Lean-verified ground truth, check ball lands in target.

Usage:
  python run_verify.py
"""
import subprocess, sys, os, time, json, shutil

UE_EXE = "C:/UE/AxisWorld-Engine/Engine/Binaries/Win64/UnrealEditor.exe"
PROJECT = "C:/Users/yanzh/projects/unreal_projects/demo2/demo1.uproject"
LEVEL = "/Game/Levels/Axis"
WS = "ws://127.0.0.1:9002"
TESTS_DIR = "C:/Users/yanzh/projects/unreal_projects/demo2/Plugins/UELivePy/Tests"
UE_CLI = os.path.join(TESTS_DIR, "utils", "ue_cli.py")
TEST_RUNNER = os.path.join(TESTS_DIR, "utils", "test_runner.py")
REC_DIR = "C:/Users/yanzh/projects/unreal_projects/demo2/Saved/Recordings"

BASE = os.path.dirname(os.path.abspath(__file__))
GROUND_TRUTH = os.path.join(BASE, "ground_truth.py")
PARAMS_FILE = os.path.join(os.path.dirname(BASE), "params.json")

def _env():
    e = os.environ.copy()
    e["MSYS_NO_PATHCONV"] = "1"
    return e

def kill_ue():
    subprocess.run(["taskkill", "/F", "/IM", "UnrealEditor.exe"],
                   env=_env(), capture_output=True)

def launch_ue():
    subprocess.Popen(
        [UE_EXE, PROJECT, LEVEL, "-game", "-windowed",
         "-ResX=1280", "-ResY=720", "-ForceRes", "-nosplash", "-log"],
        env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def wait_for_ue(timeout=60):
    for i in range(timeout // 2):
        time.sleep(2)
        try:
            r = subprocess.run(
                [sys.executable, UE_CLI, "-w", WS, "exec-string", "print('ok')"],
                capture_output=True, encoding="utf-8", errors="replace", timeout=5)
            if r.stdout and "ok" in r.stdout:
                print(f"  UE ready ({(i+1)*2}s)")
                return True
        except:
            pass
    return False

def cli(code, quiet=False):
    try:
        r = subprocess.run(
            [sys.executable, UE_CLI, "-w", WS, "exec-string", code],
            capture_output=True, encoding="utf-8", errors="replace", timeout=30)
        if not quiet and r.stdout:
            print(f"    > {r.stdout.strip()[-200:]}")
        return r.stdout or ""
    except:
        return ""

def run_script(path):
    r = subprocess.run(
        [sys.executable, TEST_RUNNER, path],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    if r.stdout:
        safe = r.stdout.strip()[-1000:].encode("ascii", "replace").decode("ascii")
        print(safe)
    if r.stderr:
        safe = r.stderr.strip()[-300:].encode("ascii", "replace").decode("ascii")
        print(f"  STDERR: {safe}")

def main():
    with open(PARAMS_FILE) as f:
        params = json.load(f)
    target = params["target"]
    target_r = params["target_radius"]

    print("=" * 60)
    print("LEAN-VERIFIED GROUND TRUTH VERIFICATION")
    print("=" * 60)
    print(f"  Target: ({target[0]}, {target[1]}), radius={target_r}")
    print()

    # Clean old log
    old_log = os.path.join(BASE, "log_for_verify.txt")
    if os.path.exists(old_log):
        os.remove(old_log)

    # Step 1: Kill existing UE
    print("[1/6] Killing existing UE...")
    kill_ue()
    time.sleep(3)

    # Step 2: Launch UE
    print("[2/6] Launching UE in game mode...")
    launch_ue()

    # Step 3: Wait for WebSocket
    print("[3/6] Waiting for UE WebSocket...")
    if not wait_for_ue(60):
        print("  FAILED: UE did not start in 60s. Aborting.")
        kill_ue()
        return

    # Step 4: Start recording + run ground truth
    print("[4/6] StartRecording + running ground_truth.py...")
    cli("import unreal_runtime as ur; ur.StartRecording(codec='h264')")
    time.sleep(0.5)
    run_script(GROUND_TRUTH)

    # Step 5: Wait for ball to land (tick task writes log_for_verify.txt)
    LOG_FILE = os.path.join(BASE, "log_for_verify.txt")
    print(f"[5/6] Waiting for ball to land (watching {os.path.basename(LOG_FILE)})...")

    result_line = None
    for tick in range(30):
        time.sleep(1)
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                # Look for first-touch line: LANDED_PASS or LANDED_FAIL
                for line in content.split("\n"):
                    if "LANDED_PASS" in line or "LANDED_FAIL" in line:
                        parts = line.split(",")
                        result_line = parts[-1].replace("LANDED_", "")
                        print(f"    First touch: frame={parts[0]}, t={parts[1]}s")
                        print(f"    Position: ({parts[2]}, {parts[3]}, {parts[4]})")
                        print(f"    Distance to target: {parts[5]} cm")
                        break
                if result_line:
                    break
            except:
                pass
        if tick % 5 == 4:
            print(f"    ...{tick+1}s")

    # Stop recording + save video
    cli("import unreal_runtime as ur; ur.StopRecording()")
    time.sleep(1)

    video_out = ""
    try:
        h264_files = [os.path.join(REC_DIR, f) for f in os.listdir(REC_DIR) if f.endswith(".h264")]
        if h264_files:
            h264 = max(h264_files, key=os.path.getmtime)
            mp4 = h264.replace(".h264", ".mp4")
            subprocess.run(["ffmpeg", "-y", "-i", h264, "-c:v", "copy", mp4],
                           capture_output=True, timeout=30)
            video_out = os.path.join(BASE, "ground_truth_result.mp4")
            shutil.copy2(mp4, video_out)
            print(f"  Video: {video_out}")
        else:
            print("  No h264 recording found")
    except Exception as e:
        print(f"  Video error: {e}")

    # Step 6: Report
    print("[6/6] Result...")
    print()
    print("=" * 60)
    if result_line:
        print(f"  RESULT: {result_line}")
    else:
        print("  Ball did not land in 30s. TIMEOUT")
    print(f"  Log: {LOG_FILE}")
    if video_out:
        print(f"  Video: {video_out}")
    print("=" * 60)

    # Cleanup
    print("\nKilling UE...")
    kill_ue()

if __name__ == "__main__":
    main()
