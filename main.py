from EBirdChecklistManager import EBirdChecklistManager
from EBirdMediaUploader import EBirdMediaUploader
from BirdReportSync import BirdReportSync

if __name__ == "__main__":
    # 上传照片
    # uploader = EBirdMediaUploader("final_merged_birds.csv")
    # uploader.run_folder_upload("S302117843", "D:\\birds\\20260219 虞山森林公园")

    # --- 更新鸟单 ---
    manager = EBirdChecklistManager("resource/观鸟记录表.csv", "resource/birding_notes.md")
    manager.sync_data()

    # 同步鸟单到观鸟记录中心
    # syncer = BirdReportSync("secrets.ini", "resource/bird_species_library.xlsx")
    # syncer.sync_to_birdreport("S302929842", 200828)