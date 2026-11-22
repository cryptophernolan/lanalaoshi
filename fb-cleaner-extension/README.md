# FB Activity Cleaner (Chrome extension)

Xoá nhanh nội dung **của chính bạn** trên Facebook thông qua **Nhật ký hoạt động** (Activity Log):
bài đăng trên trang cá nhân, bình luận ở trang bạn bè / nhóm / page, bài trong nhóm; tuỳ chọn gỡ tag và bỏ like.

## Cài đặt
1. `chrome://extensions` → bật **Developer mode** → **Load unpacked** → chọn thư mục `fb-cleaner-extension/`.
2. Đăng nhập Facebook trên Chrome như bình thường (extension **không** lưu hay nhập mật khẩu).

## Sử dụng
1. Mở `https://www.facebook.com/me/allactivity`, chọn bộ lọc ở cột trái (Bài viết của bạn, Bình luận, Nhóm, Page…).
2. Bấm icon extension → chạy **Dry-run** trước (chỉ tô viền cam các mục sẽ bị xử lý).
3. Bỏ tick Dry-run → **Start**. Có thể bấm **Stop** bất kỳ lúc nào.

## Giới hạn tốc độ & an toàn
| Tham số | Mặc định | Mức sàn (không thể đặt thấp hơn) |
|---|---|---|
| Nghỉ giữa 2 mục | 6–14 s (ngẫu nhiên) | 3 s |
| Nghỉ dài sau mỗi 20 mục | 300 s | 60 s |
| Tối đa mỗi phiên | 150 | trần 500 |

- Khi Facebook hiện **"Temporarily blocked" / "đang dùng quá nhanh" / checkpoint / xác minh danh tính**, extension **dừng ngay**
  và khoá chế độ xoá thật trong **24 giờ**.
- Facebook không công bố ngưỡng chống spam, ngưỡng thay đổi theo tuổi tài khoản và lịch sử. Mặc định đặt chậm có chủ đích;
  với tài khoản nhiều nội dung, hãy chia ra nhiều ngày (vd. 150 mục/phiên, 1–2 phiên/ngày).
- Mục "Move to trash" nằm trong Thùng rác 30 ngày, các mục "Delete" thì mất vĩnh viễn.
- Không thể xoá bài/bình luận của người khác (trừ khi bạn là admin nhóm/page — extension không xử lý trường hợp này).

## Test
```bash
node test/run-tests.js   # cần Playwright + Chromium
```
Test chạy trên `test/mock-activity-log.html` — trang **mô phỏng** UI Nhật ký hoạt động và một bộ giới hạn tốc độ giả lập
(>5 lần xoá / 30 s → hiện "Temporarily Blocked"). Kiểm tra: dry-run không xoá; tốc độ mặc định không chạm giới hạn;
cấu hình quá nhanh bị ép về mức sàn; extension tự dừng khi gặp cảnh báo; giới hạn mỗi phiên; extension nạp được vào Chromium.
**Đây không phải test với hệ thống phát hiện thật của Facebook.**

## Lưu ý
Facebook thay đổi giao diện thường xuyên; nếu extension báo "Không còn mục nào", có thể cần cập nhật nhãn trong `LABELS` của `cleaner.js`.
Tự động hoá thao tác có thể trái Điều khoản của Facebook — tự chịu trách nhiệm khi sử dụng.
