"""
Vi du mau: Pull va Inject du lieu truc tiep voi ETABS bang EtabsDataBridge.
Chay truc tiep khi ETABS dang mo mot mo hinh.
"""
import sys
from pathlib import Path

# Them thu vien vao sys.path neu chua cai dat qua pip
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from data_bridge import EtabsDataBridge


def main():
    print("=== KHOI TAO DATA BRIDGE ===")
    bridge = EtabsDataBridge()

    # Kiem tra mo hinh
    if bridge.is_locked():
        print("[CANH BAO] Mo hinh dang bi khoa. Chi co the PULL du lieu, khong the INJECT.")
    else:
        print("[THONG TIN] Mo hinh dang mo khoa, san sang PULL va INJECT.")

    # =========================================================================
    # 1. PULL DU LIEU VAO BO NHO (PANDAS DATAFRAMES)
    # =========================================================================
    print("\n--- 1. PULL DU LIEU MO HINH ---")

    # Pull danh sach bang co san
    tables = bridge.get_available_tables()
    print(f"Tong so bang du lieu co san: {len(tables)}")

    # Pull toan bo Frame (dầm, cột, tiết diện, điểm liên kết)
    df_frames = bridge.pull_frames()
    print(f"\n[DataFrame] Frames (so luong: {len(df_frames)}):")
    if not df_frames.empty:
        print(df_frames.head())

    # Pull toan bo Shell (sàn, vách, tiết diện)
    df_shells = bridge.pull_shells()
    print(f"\n[DataFrame] Shells (so luong: {len(df_shells)}):")
    if not df_shells.empty:
        print(df_shells.head())

    # Pull toan bo Joint Coordinates (tọa độ X, Y, Z)
    df_points = bridge.pull_points()
    print(f"\n[DataFrame] Points (so luong: {len(df_points)}):")
    if not df_points.empty:
        print(df_points.head())

    # =========================================================================
    # 2. INJECT DU LIEU TRUC TIEP VAO ETABS (NEU MO HINH KHONG KHOA)
    # =========================================================================
    if not bridge.is_locked():
        print("\n--- 2. INJECT DU LIEU VAO ETABS ---")

        # 2.1. Dinh nghia cac tiet dien Frame moi
        new_sections = [
            {"type": "rect", "name": "B300x600", "mat": "C30", "depth": 0.6, "width": 0.3},
            {"type": "rect", "name": "C500x500", "mat": "C30", "depth": 0.5, "width": 0.5},
            {"type": "circle", "name": "COL_D600", "mat": "C30", "dia": 0.6},
            {"type": "i", "name": "H400x200", "mat": "SS400", "depth": 0.4, "width": 0.2, "tf": 0.013, "tw": 0.008},
        ]
        sec_report = bridge.inject_frame_sections(new_sections)
        print(f"Inject Tiet dien Frame: Thanh cong={sec_report['success']}, That bai={sec_report['failed']}")

        # 2.2. Dinh nghia cac Group moi
        group_report = bridge.inject_group_definition(["GRP_COLUMNS_LEVEL1", "GRP_BEAMS_CRITICAL"])
        print(f"Inject Dinh nghia Group: Thanh cong={group_report['success']}, That bai={group_report['failed']}")

        # 2.3. Gan cau kien vao Group
        # (Vi du: lay 3 frame dau tien tu DataFrame de gan vao group)
        if not df_frames.empty:
            key = "Unique Name" if "Unique Name" in df_frames.columns else df_frames.columns[0]
            sample_frames = df_frames[key].head(3).astype(str).tolist()
            assign_report = bridge.inject_group_assignment("GRP_BEAMS_CRITICAL", {"frames": sample_frames})
            print(f"Inject Gan Group cho {sample_frames}: Gan={assign_report['assigned']}, That bai={assign_report['failed']}")

        # 2.4. Doi ten cau kien (Unique Name)
        # rename_sample = [
        #     {"type": "frame", "old_name": "1", "new_name": "C1_AXIS_A1"},
        #     {"type": "shell", "old_name": "3", "new_name": "SLAB_STORY1_01"}
        # ]
        # rename_report = bridge.inject_names(rename_sample)
        # print(f"Inject Doi ten: Thanh cong={rename_report['success']}, That bai={rename_report['failed']}")

    print("\n=== HOAN TAT DONG BO DU LIEU ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Loi: {e}")
