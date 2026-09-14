# Changelog - etabs_python

## [3.1.1] - 2026-09-14

### Added
- **Operation column_layout_tables trong etabs_python.ops_model**:
  - Trích xuất toàn bộ bảng hình học cột, vách, dầm, điểm và định nghĩa group phục vụ ứng dụng Column & Wall Layout HTML Viewer.
  - Tự động nhận diện các bảng kết quả thiết kế có hậu tố tiêu chuẩn (ví dụ: Concrete Column Design Summary - ACI 318-19).
  - Hỗ trợ thanh tiến trình chi tiết qua ctx.progress.start() và ctx.progress.step().
- **Unit test**:
  - Bổ sung test_column_layout_tables trong tests/test_ops_offline.py. 12/12 unit tests đạt 100%.
- **Design-code compatibility fixtures**:
  - ACI 318-19, AISC 360-16, Eurocode 2-2004, Eurocode 3-2005,
    SP 63.13330-2012, BS 8110-97 và BS 5950-2000.
  - Lưu field keys, display names, units, status/warning/error và example results;
    loại trừ Program Control và thông tin license.
- **Global result operations**:
  - Base/joint reactions, story and diaphragm drifts, story forces/stiffness,
    modal periods/participation, centers of mass/rigidity và tributary area/LLRF.

### Changed
- **Ánh xạ tên cột (FieldKey -> FieldName)**:
  - Khi đọc trực tiếp từ ETABS COM API qua GetTableForDisplayArray, ETABS trả về mã trường nội bộ (FieldKey, ví dụ: AnalysisSect, Thickness, SectProp, UniqueName).
  - Đã bổ sung logic phân giải tên hiển thị:
    1. Tự động truy vấn sap_model.DatabaseTables.GetAllFieldsInTable(table_name) để ánh xạ trực tiếp sang FieldName chuẩn của ETABS GUI.
    2. Bổ sung từ điển tĩnh KNOWN_FIELD_KEY_TO_NAME làm fallback cho các môi trường offline / file.
  - Các cột được chuẩn hóa sang định dạng hiển thị:
    - AnalysisSect -> Analysis Section
    - DesignSect -> Design Section
    - AxisAngle -> Axis Angle
    - Type -> Design Type
    - Thickness -> Wall Thickness
    - SectProp -> Section Property
    - PropType -> Property Type
    - GroupName -> Group Name
    - ObjectType -> Object Type
    - UniqueName (trong Group Assignments) -> Object Unique Name
    - PierName -> Pier Name

### Robustness
- Trích xuất mảng units cho từng bảng trong column_layout_tables từ df.attrs['units'] (đơn vị đã chuyển đổi theo Engine: length='m', section='mm') ưu tiên hơn COM present units (tránh tình trạng tọa độ điểm đã ở mét nhưng bị gán nhãn mm dẫn đến client chia 1000 lần thứ hai).
- Giúp các ứng dụng web client (Canvas/SVG viewer) đọc đúng đơn vị đo lường (m vs mm), chống tràn kích thước điểm ảnh và lỗi bộ nhớ đồ họa.
- Hỗ trợ tên bảng `Concrete Column PMM Shear Envelope` riêng của SP 63.
- Trả danh sách warning khi bảng live không đọc được thay vì bỏ lỗi âm thầm.
- Hỗ trợ pandas 3 string dtype và scalar numeric trong các tham số filter.
