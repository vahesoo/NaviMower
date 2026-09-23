"""Pure Pillow renderer for cached Navimower map snapshots."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import math
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Map Card H2 SVG snapshot subset: preserve the recognisable body/deck/nose layers.
MOWER_ART_WIDTH = 120.0
MOWER_ART_HEIGHT = 159.0

# (translate_x, translate_y, SVG path d, fill)
H2_SNAPSHOT_SVG_PATHS = (
    (58.375, 2.75, "m0 0c1.2942-0.0077344 2.5884-0.015469 3.9219-0.023438 14.29 0.028524 29.546 0.24465 40.859 10.164 6.1186 6.5853 9.2061 13.182 9.8438 22.109l-3 2c0.27715 0.53109 0.5543 1.0622 0.83984 1.6094 0.54334 1.0596 0.54334 1.0596 1.0977 2.1406 0.35965 0.69609 0.7193 1.3922 1.0898 2.1094 3.405 7.4938 2.1793 14.42-0.37109 21.941-0.88559 2.9678-0.64728 5.6517-0.58594 8.7461 0.018574 1.401 0.036791 2.8021 0.054688 4.2031 0.016758 0.69094 0.033516 1.3819 0.050781 2.0938 0.044808 4.7676-0.67052 8.6286-2.1758 13.156l5 1c3.2436 16.189 5.157 38.035 0 54-2.8808 2.357-3.5267 2.5046-7 3-0.99387 0.25523-1.9877 0.51047-3.0117 0.77344-14.11 3.5075-28.668 3.3958-43.125 3.3569-2.3622-0.0053675-4.7242-8.92e-6 -7.0864 0.0063477-26.919 0.01591-26.919 0.01591-39.59-3.0742-0.76119-0.16951-1.5224-0.33902-2.3066-0.51367-3.4467-0.80008-5.909-1.5676-8.8809-3.5488-4.025-14.507-5.8515-41.297 1-55h4l-0.875-3.125c-4.859-19.957-7.9418-52.674 3.2266-71.105 5.4442-7.6638 12.217-12.36 21.44-14.381 8.5661-1.371 16.917-1.6897 25.583-1.6382z", "#3D434E"),
    (98, 26, "m0 0 2.875 1.5625c2.4157 1.6342 2.9116 2.8489 4.125 5.4375 1.3239 1.0124 2.656 2.0144 4 3v2h2c3.039 5.8248 3.3541 10.482 3 17l-2 1c-0.086367-0.60586-0.17273-1.2117-0.26172-1.8359-1.5688-9.3554-4.6411-17.641-12.368-23.58-8.8635-5.9236-18.14-6.1299-28.495-5.8965-1.3385 0.011522-2.6771 0.021298-4.0156 0.029297-5.8083-0.4891-5.8083-0.4891-10.859 1.2832-0.29365 4.8229-0.31431 9.6488-0.3501 14.48-0.01644 1.6225-0.043569 3.245-0.082031 4.8672-0.25092 10.642-0.2235 19.483 7.3777 27.776 5.3326 5.1841 10.989 7.9255 18.429 8.2524 10.875-0.50362 17.07-4.7071 24.625-12.375l1 3c-1.3633 2.043-1.3633 2.043-3.3125 4.1875-0.63551 0.71543-1.271 1.4309-1.9258 2.168-1.7617 1.6445-1.7617 1.6445-3.7617 1.6445-0.18176 0.99258-0.36352 1.9852-0.55078 3.0078-7.4631 40.493-7.4631 40.493-16.449 49.992-4.4566 2.6702-7.7691 3.3837-12.938 3.3984-1.3071 0.0038672-2.6142 0.0077344-3.9609 0.011719-0.67386-0.0062842-1.3477-0.012568-2.042-0.019043-2.0544-0.016073-4.1068-1.1502e-4 -6.1611 0.019043-1.9607-0.0058008-1.9607-0.0058008-3.9609-0.011719-1.1872-0.0033838-2.3745-0.0067676-3.5977-0.010254-5.6201-0.65321-9.5243-2.9407-13.027-7.3257-3.2308-4.374-4.5661-8.4789-5.6094-13.777-0.58335-2.7256-1.3169-5.2242-2.1758-7.8672-1.8208-5.9843-2.5713-11.959-3.2148-18.168-0.18889-1.7978-0.18889-1.7978-0.38159-3.6318-0.88855-9.3006-1.2187-18.533-1.2434-27.868-0.005398-1.2539-0.010796-2.5079-0.016357-3.7998 0.049626-6.3469 0.46255-11.881 2.3289-17.95 0.1998-0.83789 0.39961-1.6758 0.60547-2.5391 1.3526-4.8476 3.0758-8.2723 7.1719-11.309 14.79-7.668 52.232-6.9718 65.223 3.8477z", "#393937"),
    (58.375, 2.75, "m0 0c1.2942-0.0077344 2.5884-0.015469 3.9219-0.023438 14.29 0.028524 29.546 0.24465 40.859 10.164 6.1186 6.5853 9.2061 13.182 9.8438 22.109l-3 1-0.6875-3.375c-1.9931-7.9442-5.4526-15.549-12.547-20.039-13.047-6.2805-26.987-5.7675-41.141-5.8359-0.69532-0.0054483-1.3906-0.010897-2.107-0.01651-14.69-0.24195-14.69-0.24195-28.33 4.579-0.68707 0.38801-1.3741 0.77602-2.082 1.1758-4.0728 2.9243-6.7336 5.0437-7.9961 10.027l-0.35938 3.7344c-0.69444 6.6944-0.69444 6.6944-1.75 7.75-1.9256 7.883-2.5121 15.924-3 24l2 1c-0.10699 1.2439-0.21398 2.4879-0.32422 3.7695-0.95407 12.271-0.95407 12.271 1.7305 24.199 0.59375 2.0312 0.59375 2.0312-0.40625 5.0312l2 1c0.6663 3.3981 1.0334 6.4768 0.8125 9.9375 0.22681 3.7046 1.4774 5.9596 3.3125 9.1328 0.875 1.9297 0.875 1.9297 0.875 5.9297h-2v4h3c2.0344 3.7297 3 5.6691 3 10h2l1.3125 2.25c3.1739 5.1722 7.2821 7.349 12.892 9.1528 5.6571 1.2083 11.365 0.74384 17.108 0.47217 1.242-0.036094 2.484-0.072188 3.7637-0.10938 9.4101-0.37502 21.041-1.0827 28.924-6.7656 3.898-4.6532 7.1555-8.8804 8-15l4-1c0.10184-1.1034 0.20367-2.2069 0.30859-3.3438 0.65448-5.2359 2.1807-10.134 3.7539-15.156 0.28166-0.91781 0.56332-1.8356 0.85352-2.7812 0.68879-2.2415 1.3835-4.4809 2.084-6.7188 1.6661-0.042721 3.3338-0.040638 5 0 4.9697 4.9697 3.2332 20.828 3.25 27.438-0.1474 21.053-0.1474 21.053-2.25 27.562-2.8808 2.357-3.5267 2.5046-7 3-0.99387 0.25523-1.9877 0.51047-3.0117 0.77344-14.11 3.5075-28.668 3.3958-43.125 3.3569-2.3622-0.0053675-4.7242-8.92e-6 -7.0864 0.0063477-26.919 0.01591-26.919 0.01591-39.59-3.0742-0.76119-0.16951-1.5224-0.33902-2.3066-0.51367-3.4467-0.80008-5.909-1.5676-8.8809-3.5488-4.025-14.507-5.8515-41.297 1-55h4l-0.875-3.125c-4.859-19.957-7.9418-52.674 3.2266-71.105 5.4442-7.6638 12.217-12.36 21.44-14.381 8.5661-1.371 16.917-1.6897 25.583-1.6382z", "#586270"),
    (70.938, 23.625, "m0 0c1.282-0.024492 2.5639-0.048984 3.8848-0.074219 10.99-0.033625 20.28 1.6864 28.455 9.4414 2.8682 3.3429 4.8421 7.0449 6.7227 11.008l1 2c0.98819 10.238-0.74686 18.36-7.3125 26.375-6.0064 6.3522-13.289 9.5431-22.062 10-7.7458-0.22356-14.23-3.959-19.711-9.2834-7.5493-8.2494-7.6241-17.105-7.4011-27.69 0.050035-2.4248 0.061526-4.8476 0.067139-7.2729 0.014065-1.543 0.030262-3.086 0.048828-4.6289 0.0053627-0.72389 0.010725-1.4478 0.016251-2.1936 0.24214-10.122 8.1561-7.6734 16.292-7.6814z", "#B9B9BD"),
    (111, 49, "m0 0h1l1 6 1-6h1c0.32312 5.8161-0.47343 10.285-2.3438 15.801-0.8334 2.7929-0.67269 5.0569-0.33984 7.9375 0.96884 14.071-4.0337 27.009-8.3767 40.194-0.82268 2.6856-1.4505 5.3047-1.9397 8.0676h8v1h-12v5c-1.3555 2.5703-1.3555 2.5703-3.1875 5.125-0.59168 0.8482-1.1834 1.6964-1.793 2.5703-3.2298 3.6859-6.9111 5.4531-11.52 6.9297-0.73219 0.23977-1.4644 0.47953-2.2188 0.72656-7.0865 2.0143-14.208 2.1009-21.531 2.3984-1.2852 0.06832-2.5704 0.13664-3.8945 0.20703-8.71 0.3484-16.097-0.017236-23.355-5.207-3.0202-3.3223-3.5-5.3008-3.5-9.75l2-2c0.24287-2.4401 0.24287-2.4401 0.125-5.125-0.018047-0.91008-0.036094-1.8202-0.054688-2.7578-0.023203-0.69867-0.046406-1.3973-0.070312-2.1172h3l0.625 3c1.3412 4.6705 3.9148 8.6023 7.375 12 5.707 2.8372 11.566 2.3721 17.81 2.3706 2.0524 0.0043679 4.1027 0.040712 6.1548 0.078613 9.7525 0.078038 9.7525 0.078038 18.035-4.4492 3.2338-4.6711 5.1927-9.6234 7-15 0.31969-0.90105 0.63938-1.8021 0.96875-2.7305 2.5188-7.5278 3.9338-14.978 4.9922-22.822 0.10828-0.77819 0.21656-1.5564 0.32812-2.3582 0.089751-0.69231 0.1795-1.3846 0.27197-2.0979 1.0914-4.9527 3.5283-6.9397 7.439-9.9919 2.6844-2.972 3.3079-5.1068 4-9 0.48752-1.421 0.98896-2.8373 1.5-4.25 1.5825-4.5931 2.1163-8.9154 2.5-13.75z", "#444B56"),
    (58.375, 2.75, "m0 0c1.2942-0.0077344 2.5884-0.015469 3.9219-0.023438 14.29 0.028524 29.546 0.24465 40.859 10.164 6.1186 6.5853 9.2061 13.182 9.8438 22.109l-3 1-0.6875-3.375c-1.9931-7.9442-5.4526-15.549-12.547-20.039-13.047-6.2805-26.987-5.7675-41.141-5.8359-0.69532-0.0054483-1.3906-0.010897-2.107-0.01651-14.69-0.24195-14.69-0.24195-28.33 4.579-0.68707 0.38801-1.3741 0.77602-2.082 1.1758-4.0728 2.9243-6.7336 5.0437-7.9961 10.027l-0.35938 3.7344c-0.69444 6.6944-0.69444 6.6944-1.75 7.75-1.9256 7.883-2.5121 15.924-3 24l2 1c-0.10699 1.2439-0.21398 2.4879-0.32422 3.7695-0.95407 12.271-0.95407 12.271 1.7305 24.199 0.59375 2.0312 0.59375 2.0312-0.40625 5.0312l2 1c0.6663 3.3981 1.0334 6.4768 0.8125 9.9375 0.22681 3.7046 1.4774 5.9596 3.3125 9.1328 0.875 1.9297 0.875 1.9297 0.875 5.9297h-2v5l-3.75 0.375c-3.1527 0.31527-5.7259 0.63227-8.25 2.625-0.70788 3.3398-0.70788 3.3398-0.75 7.125-0.056719 1.2813-0.11344 2.5627-0.17188 3.8828-0.038672 1.4811-0.038672 1.4811-0.078125 2.9922h-2c-1.0896-6.1974-1.1919-12.279-1.1875-18.562 8.057e-5 -1.0611 1.6113e-4 -2.1221 2.4414e-4 -3.2153 0.1385-19.125 0.1385-19.125 3.1873-25.222h4l-0.875-3.125c-4.859-19.957-7.9418-52.674 3.2266-71.105 5.4442-7.6638 12.217-12.36 21.44-14.381 8.5661-1.371 16.917-1.6897 25.583-1.6382z", "#4D3A32"),
    (46.812, 23.875, "m0 0c1.0393 0.013535 1.0393 0.013535 2.0996 0.027344 1.6961 0.023395 3.392 0.05926 5.0879 0.097656v2c-1.4115 0.02127-1.4115 0.02127-2.8516 0.042969-8.034-0.020155-8.034-0.020155-14.898 3.5195-0.7425 0.80438-1.485 1.6088-2.25 2.4375h-2v3h-2c-2.1685 7.0305-2.308 13.611-2.25 20.938 0.0060425 1.224 0.012085 2.4481 0.018311 3.7092 0.16576 15.599 0.45834 31.627 4.5872 46.76 0.63983 2.5748 0.86935 4.9505 1.0195 7.5938 0.31074 3.3933 0.62507 4.0001 2.875 6.875 7.2719 5.6192 12.497 6.306 21.438 6.3125 0.86045 0.012246 1.7209 0.024492 2.6074 0.037109 7.1038 0.01606 14.486-0.45579 20.705-4.2246 1.0759-2.7244 1.0759-2.7244 2-6 0.95309-1.9221 1.9602-3.8032 3-5.6797 2.163-5.0187 3.0421-10.082 3.5352-15.484 1.3269-14.389 1.3269-14.389 4.4492-18.359 2.287-1.4505 4.5103-2.4625 7.0156-3.4766 1.9147-1.3833 1.9147-1.3833 3.5625-2.875l2.4375-2.125c-0.59699 3.3314-1.7201 5.2128-3.9375 7.75-0.52465 0.61359-1.0493 1.2272-1.5898 1.8594-1.4727 1.3906-1.4727 1.3906-3.4727 1.3906-0.18176 0.99258-0.36352 1.9852-0.55078 3.0078-7.4631 40.493-7.4631 40.493-16.449 49.992-4.4566 2.6702-7.7691 3.3837-12.938 3.3984-1.3071 0.0038672-2.6142 0.0077344-3.9609 0.011719-0.67386-0.0062842-1.3477-0.012568-2.042-0.019043-2.0544-0.016073-4.1068-1.1502e-4 -6.1611 0.019043-1.9607-0.0058008-1.9607-0.0058008-3.9609-0.011719-1.1872-0.0033838-2.3745-0.0067676-3.5977-0.010254-5.6201-0.65321-9.5243-2.9407-13.027-7.3257-3.2308-4.374-4.5661-8.4789-5.6094-13.777-0.58335-2.7256-1.3169-5.2242-2.1758-7.8672-1.8208-5.9843-2.5713-11.959-3.2148-18.168-0.18889-1.7978-0.18889-1.7978-0.38159-3.6318-0.88855-9.3006-1.2187-18.533-1.2434-27.868-0.005398-1.2539-0.010796-2.5079-0.016357-3.7998 0.0709-9.0677 0.47628-16.593 6.3289-23.95 5.5848-5.1552 10.334-6.3111 17.812-6.125z", "#191B1D"),
    (58, 39, "m0 0 2 3 4-1c-0.875 4.75-0.875 4.75-2 7h3l1-3c1.8493 1.8493 1.0444 5.2134 1.0625 7.6875 0.2307 5.1797 0.2307 5.1797 2.7109 9.5391 2.0215 1.4815 3.9701 2.689 6.2266 3.7734l2 1c5.0928 0.25046 8.3025 0.031346 13-2 2.8125-0.125 2.8125-0.125 5 0-1 3-1 3-3 4-0.65555 2.5273-0.65555 2.5273-1 5l1.875 0.375c2.125 0.625 2.125 0.625 4.125 2.625-6.4549 4.3032-13.322 6.0195-21 5-8.5725-2.4388-15.512-7.885-20-15.562-3.4239-7.5765-3.3654-16.543-0.5-24.312l1.5-3.125z", "#BBBABE"),
    (90.84, 40.047, "m0 0c3.6637 2.9023 5.839 5.6904 6.4961 10.32 0.089546 5.7488-0.38751 9.136-4.3359 13.633-4.0013 3.5163-7.5955 4.3598-12.938 4.2812-3.4438-0.46961-5.4725-2.0526-8.0625-4.2812-3.8732-5.6095-5.0661-10.207-4-17 2.1148-4.0174 4.84-6.3813 8.8125-8.5 4.867-0.76344 9.6269-0.95699 14.027 1.5469z", "#3A3937"),
    (58.375, 2.75, "m0 0c1.2942-0.0077344 2.5884-0.015469 3.9219-0.023438 14.29 0.028524 29.546 0.24465 40.859 10.164 6.1186 6.5853 9.2061 13.182 9.8438 22.109l-3 1-0.6875-3.375c-1.9931-7.9442-5.4526-15.549-12.547-20.039-13.047-6.2805-26.987-5.7675-41.141-5.8359-0.69532-0.0054483-1.3906-0.010897-2.107-0.01651-13.536-0.078771-24.412 0.81857-35.268 9.454-11.009 13.762-9.6106 31.431-9.875 48.125-0.038266 2.0124-0.077323 4.0248-0.11719 6.0371-0.095042 4.8833-0.17941 9.7668-0.25781 14.65h-1c-3.5786-19.011-6.1324-48.929 4.3516-66.23 5.4442-7.6638 12.217-12.36 21.44-14.381 8.5661-1.371 16.917-1.6897 25.583-1.6382z", "#E38A51"),
    (59.889, 23.773, "m0 0c1.2045 0.010474 2.4089 0.020947 3.6499 0.031738 1.9529 0.009668 1.9529 0.009668 3.9453 0.019531 1.3802 0.016712 2.7604 0.033642 4.1406 0.050781 1.388 0.01003 2.776 0.019156 4.1641 0.027344 3.4038 0.023638 6.8073 0.056588 10.211 0.097656v1c-0.94102 0.097969-1.882 0.19594-2.8516 0.29688-9.3425 0.9875-9.3425 0.9875-17.148 5.7031 0.99 0.33 1.98 0.66 3 1v-3c1.9375 0.3125 1.9375 0.3125 4 1l1 3c2.0151 0.73324 2.0151 0.73324 4 1v2l3.3125-0.0625c3.6875 0.0625 3.6875 0.0625 6.6875 1.0625v2l-3.5625-0.1875c-5.5512 0.01965-9.6169 1.647-13.875 5.25-2.8139 5.2902-2.17 11.279-0.5625 16.938-0.99-0.33-1.98-0.66-3-1l-1-14-1 2h-3l1-6h-4c-2.1634 2.0327-2.1634 2.0327-4 5-0.05789-3.4583-0.093558-6.9164-0.125-10.375-0.016758-0.98484-0.033516-1.9697-0.050781-2.9844-0.0064453-0.94102-0.012891-1.882-0.019531-2.8516-0.010474-0.86931-0.020947-1.7386-0.031738-2.6343 0.37662-3.5743 1.6701-4.111 5.1162-4.3818z", "#B9B7B8"),
    (98, 26, "m0 0 2.875 1.5625c2.4157 1.6342 2.9116 2.8489 4.125 5.4375 1.3239 1.0124 2.656 2.0144 4 3v2h2c3.039 5.8248 3.3541 10.482 3 17l-2 1c-0.086367-0.60586-0.17273-1.2117-0.26172-1.8359-1.5919-9.4929-4.7384-17.59-12.457-23.695-13.179-8.4853-30.22-6.7807-45.281-6.4688-1.0565 0.018853-1.0565 0.018853-2.1343 0.038086-10.033 0.23249-16.998 1.4058-24.866 7.9619 0.10719-3.6446 1.248-5.6731 3.75-8.1875 13.328-10.349 54.465-8.461 67.25 2.1875z", "#2B3039"),
)

SNAPSHOT_SIZE = 1024
CUTTING_ACTIONS = {5, 8}

_BACKGROUND = (246, 248, 246, 255)
_ZONE_FILL = (129, 199, 132, 54)
_ZONE_STROKE = (67, 160, 71, 220)
_MOWED = (46, 125, 50, 138)
_ROUTE = (27, 94, 32, 220)
_OFF_LIMIT = (255, 90, 0, 145)
_VF_OFF = (54, 112, 210, 110)
_CHANNEL = (97, 97, 97, 175)
_GATE = (123, 67, 151, 110)
_DOCK = (55, 71, 79, 255)
_TEXT = (55, 71, 79, 255)



_SVG_PATH_TOKEN_RE = re.compile(
    r"[MmLlHhVvCcZz]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)


@lru_cache(maxsize=128)
def _svg_path_polygons(path_d: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Sample the small SVG command subset used by the embedded mower artwork."""
    tokens = _SVG_PATH_TOKEN_RE.findall(path_d)
    polygons: list[tuple[tuple[float, float], ...]] = []
    current: list[tuple[float, float]] = []
    command: str | None = None
    x = y = 0.0
    start_x = start_y = 0.0
    index = 0

    def number() -> float:
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        token = tokens[index]
        if token in "MmLlHhVvCcZz":
            command = token
            index += 1
            if command in "Zz":
                if current:
                    if current[-1] != (start_x, start_y):
                        current.append((start_x, start_y))
                    if len(current) >= 3:
                        polygons.append(tuple(current))
                current = []
                x, y = start_x, start_y
                command = None
            continue
        if command is None:
            index += 1
            continue

        relative = command.islower()
        op = command.lower()
        if op == "m":
            nx, ny = number(), number()
            if relative:
                nx += x
                ny += y
            if current and len(current) >= 3:
                polygons.append(tuple(current))
            current = [(nx, ny)]
            x, y = nx, ny
            start_x, start_y = x, y
            command = "l" if relative else "L"
        elif op == "l":
            nx, ny = number(), number()
            if relative:
                nx += x
                ny += y
            x, y = nx, ny
            current.append((x, y))
        elif op == "h":
            nx = number()
            x = x + nx if relative else nx
            current.append((x, y))
        elif op == "v":
            ny = number()
            y = y + ny if relative else ny
            current.append((x, y))
        elif op == "c":
            x1, y1, x2, y2, nx, ny = (
                number(),
                number(),
                number(),
                number(),
                number(),
                number(),
            )
            if relative:
                x1, y1 = x + x1, y + y1
                x2, y2 = x + x2, y + y2
                nx, ny = x + nx, y + ny
            origin_x, origin_y = x, y
            for step in range(1, 7):
                t = step / 6.0
                one = 1.0 - t
                px = (
                    one**3 * origin_x
                    + 3 * one**2 * t * x1
                    + 3 * one * t**2 * x2
                    + t**3 * nx
                )
                py = (
                    one**3 * origin_y
                    + 3 * one**2 * t * y1
                    + 3 * one * t**2 * y2
                    + t**3 * ny
                )
                current.append((px, py))
            x, y = nx, ny
        else:
            # The snapshot artwork intentionally contains only M/L/H/V/C/Z.
            break

    if current and len(current) >= 3:
        polygons.append(tuple(current))
    return tuple(polygons)


def _hex_rgba(value: str) -> tuple[int, int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return (61, 67, 78, 255)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255)


@lru_cache(maxsize=1)
def _mower_art_layers() -> tuple[
    tuple[tuple[tuple[float, float], ...], tuple[int, int, int, int]], ...
]:
    layers = []
    for translate_x, translate_y, path_d, fill in H2_SNAPSHOT_SVG_PATHS:
        for polygon in _svg_path_polygons(path_d):
            layers.append(
                (
                    tuple(
                        (point[0] + translate_x, point[1] + translate_y)
                        for point in polygon
                    ),
                    _hex_rgba(fill),
                )
            )
    return tuple(layers)


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _xy_points(value: Any) -> list[list[float]]:
    if isinstance(value, dict):
        for key in ("polygon", "points", "path"):
            if key in value:
                return _xy_points(value.get(key))
        return []
    if not isinstance(value, (list, tuple)):
        return []
    result: list[list[float]] = []
    for raw in value:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        x = _as_float(raw[0])
        y = _as_float(raw[1])
        if x is None or y is None:
            continue
        result.append([x, y])
    return result


def _session_cutting_segments(session: Any) -> list[list[list[float]]]:
    """Mirror the session archive's conservative blade-on edge classification."""
    if not isinstance(session, dict):
        return []
    starts = {
        stamp
        for raw in session.get("segment_starts_ms") or []
        if (stamp := _as_int(raw)) is not None
    }
    points: list[tuple[int, float, float, bool, int | None]] = []
    for raw in session.get("points") or []:
        if not isinstance(raw, list) or len(raw) < 3:
            continue
        stamp = _as_int(raw[0])
        x = _as_float(raw[1])
        y = _as_float(raw[2])
        if stamp is None or x is None or y is None:
            continue
        action = _as_int(raw[6]) if len(raw) > 6 else None
        activity = str(raw[4] if len(raw) > 4 else "").strip().lower()
        if action is not None:
            cutting = action in CUTTING_ACTIONS
        else:
            cutting = (
                activity not in {"docked", "paused", "returning", "error"}
                and (activity == "mowing" or "mow" in activity or "cut" in activity)
            )
        zone_id = _as_int(raw[7]) if len(raw) > 7 else None
        points.append((stamp, x, y, cutting, zone_id))

    fragments: list[list[tuple[int, float, float, bool, int | None]]] = []
    current: list[tuple[int, float, float, bool, int | None]] = []
    for point in points:
        if current and point[0] in starts:
            fragments.append(current)
            current = []
        current.append(point)
    if current:
        fragments.append(current)

    result: list[list[list[float]]] = []
    for fragment in fragments:
        segment: list[list[float]] = []
        for previous, current_point in zip(fragment, fragment[1:]):
            edge_cutting = (
                previous[3]
                and current_point[3]
                and (previous[4] is not None or current_point[4] is not None)
            )
            start_xy = [previous[1], previous[2]]
            end_xy = [current_point[1], current_point[2]]
            if not edge_cutting or start_xy == end_xy:
                if len(segment) >= 2:
                    result.append(segment)
                segment = []
                continue
            if not segment:
                segment = [start_xy, end_xy]
            else:
                if segment[-1] != start_xy:
                    segment.append(start_xy)
                segment.append(end_xy)
        if len(segment) >= 2:
            result.append(segment)
    return result


def _static_points(source: dict[str, Any]) -> list[list[float]]:
    map_data = source.get("map") or {}
    result: list[list[float]] = []
    for zone in map_data.get("zones") or []:
        result.extend(_xy_points(zone))
    for key in ("off_limit_areas", "vf_off_areas"):
        for area in map_data.get(key) or []:
            result.extend(_xy_points(area))
    for gate in source.get("gate_areas") or []:
        result.extend(_xy_points(gate))
    station = map_data.get("station") or {}
    sx = _as_float(station.get("x")) if isinstance(station, dict) else None
    sy = _as_float(station.get("y")) if isinstance(station, dict) else None
    if sx is not None and sy is not None:
        result.append([sx, sy])
    return result


def _projector(points: list[list[float]], size: int):
    if not points:
        points = [[-5.0, -5.0], [5.0, 5.0]]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 0.5)
    span_y = max(max_y - min_y, 0.5)
    pad = max(span_x, span_y) * 0.08 + 0.5
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad
    span_x = max_x - min_x
    span_y = max_y - min_y
    scale = min((size - 1) / span_x, (size - 1) / span_y)
    draw_w = span_x * scale
    draw_h = span_y * scale
    offset_x = (size - draw_w) / 2.0
    offset_y = (size - draw_h) / 2.0

    def project(point: list[float] | tuple[float, float]) -> tuple[float, float]:
        return (
            offset_x + (float(point[0]) - min_x) * scale,
            offset_y + (max_y - float(point[1])) * scale,
        )

    return project, scale


def _draw_round_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    if len(points) < 2:
        return
    draw.line(points, fill=fill, width=max(1, width), joint="curve")
    radius = max(1, width) / 2.0
    for x, y in (points[0], points[-1]):
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=fill,
        )


def _draw_polygon_layer(
    image: Image.Image,
    values: Any,
    project,
    *,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
    outline_width: int = 2,
) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for value in values or []:
        points = _xy_points(value)
        if len(points) < 3:
            continue
        projected = [project(point) for point in points]
        draw.polygon(projected, fill=fill)
        draw.line(projected + [projected[0]], fill=outline, width=outline_width, joint="curve")
    image.alpha_composite(layer)


def _draw_zone_labels(
    image: Image.Image,
    zones: Any,
    project,
    *,
    size: int,
) -> None:
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=max(12, round(size / 64)))
    except TypeError:
        font = ImageFont.load_default()
    for zone in zones or []:
        if not isinstance(zone, dict):
            continue
        points = _xy_points(zone)
        name = str(zone.get("name") or "").strip()
        if len(points) < 3 or not name:
            continue
        cx = sum(point[0] for point in points) / len(points)
        cy = sum(point[1] for point in points) / len(points)
        x, y = project([cx, cy])
        bbox = draw.textbbox((0, 0), name, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        pad_x, pad_y = 5, 3
        draw.rounded_rectangle(
            (x - w / 2 - pad_x, y - h / 2 - pad_y, x + w / 2 + pad_x, y + h / 2 + pad_y),
            radius=6,
            fill=(245, 247, 248, 225),
            outline=(176, 190, 197, 220),
            width=1,
        )
        draw.text((x - w / 2, y - h / 2 - bbox[1]), name, font=font, fill=_TEXT)


def _draw_station(image: Image.Image, station: Any, project, scale: float) -> None:
    if not isinstance(station, dict):
        return
    x = _as_float(station.get("x"))
    y = _as_float(station.get("y"))
    if x is None or y is None:
        return
    px, py = project([x, y])
    radius = max(7.0, min(15.0, scale * 0.35))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (px - radius, py - radius * 0.8, px + radius, py + radius * 0.8),
        radius=max(3, radius * 0.25),
        fill=_DOCK,
        outline=(255, 255, 255, 255),
        width=2,
    )
    draw.line(
        [(px + 1, py - radius * 0.45), (px - 3, py), (px + 1, py), (px - 1, py + radius * 0.45)],
        fill=(105, 240, 174, 255),
        width=max(2, round(radius / 4)),
    )


def _draw_mower(image: Image.Image, position: Any, project, scale: float) -> None:
    """Draw the Map Card mower SVG artwork at the live local-map pose."""
    if not isinstance(position, dict):
        return
    x = _as_float(position.get("x"))
    y = _as_float(position.get("y"))
    if x is None or y is None:
        return
    heading = _as_float(position.get("heading"))
    if heading is None:
        heading = 0.0

    px, py = project([x, y])
    # Keep notification snapshots legible on both very large and small maps.
    target_height = max(34.0, min(64.0, image.width / 18.0))
    artwork_scale = target_height / MOWER_ART_HEIGHT

    # The Map Card artwork faces SVG-up. Coordinator heading is the raw vendor postureTheta in radians.
    # heading=0 points along local +X. Replicate the
    # card's screen transform: rotate(90deg - heading).
    angle = math.pi / 2.0 - heading
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    center_x = MOWER_ART_WIDTH / 2.0
    center_y = MOWER_ART_HEIGHT / 2.0

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for layer_index, (polygon, fill) in enumerate(_mower_art_layers()):
        transformed = []
        for art_x, art_y in polygon:
            dx = (art_x - center_x) * artwork_scale
            dy = (art_y - center_y) * artwork_scale
            transformed.append(
                (
                    px + dx * cos_a - dy * sin_a,
                    py + dx * sin_a + dy * cos_a,
                )
            )
        if len(transformed) < 3:
            continue
        draw.polygon(
            transformed,
            fill=fill,
            outline=(255, 255, 255, 235) if layer_index == 0 else None,
            width=max(1, round(image.width / 512)) if layer_index == 0 else 1,
        )
    image.alpha_composite(layer)


def _draw_placeholder(image: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=20)
    except TypeError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (image.width - (bbox[2] - bbox[0])) / 2
    y = (image.height - (bbox[3] - bbox[1])) / 2
    draw.text((x, y), text, font=font, fill=(100, 110, 105, 255))


def render_snapshot_png(source: dict[str, Any], size: int = SNAPSHOT_SIZE) -> bytes:
    """Render one backend-owned latest-map snapshot as PNG bytes."""
    size = max(320, min(1600, int(size)))
    image = Image.new("RGBA", (size, size), _BACKGROUND)
    map_data = source.get("map") or {}
    static = _static_points(source)
    position = source.get("position") or {}
    if not static:
        px = _as_float(position.get("x")) if isinstance(position, dict) else None
        py = _as_float(position.get("y")) if isinstance(position, dict) else None
        if px is not None and py is not None:
            static = [[px - 5.0, py - 5.0], [px + 5.0, py + 5.0]]
    project, scale = _projector(static, size)

    zones = map_data.get("zones") or []
    _draw_polygon_layer(
        image,
        zones,
        project,
        fill=_ZONE_FILL,
        outline=_ZONE_STROKE,
        outline_width=max(1, round(size / 512)),
    )
    _draw_polygon_layer(
        image,
        map_data.get("off_limit_areas") or [],
        project,
        fill=_OFF_LIMIT,
        outline=(216, 67, 21, 220),
    )
    _draw_polygon_layer(
        image,
        map_data.get("vf_off_areas") or [],
        project,
        fill=_VF_OFF,
        outline=(41, 98, 180, 220),
    )
    _draw_polygon_layer(
        image,
        source.get("gate_areas") or [],
        project,
        fill=_GATE,
        outline=(94, 53, 177, 230),
    )

    channel_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    channel_draw = ImageDraw.Draw(channel_layer)
    for channel in map_data.get("channels") or []:
        points = _xy_points(channel)
        if len(points) < 2:
            continue
        projected = [project(point) for point in points]
        _draw_round_line(
            channel_draw,
            projected,
            fill=_CHANNEL,
            width=max(2, round(size / 256)),
        )
    image.alpha_composite(channel_layer)

    mowed_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    mowed_draw = ImageDraw.Draw(mowed_layer)
    width_m = _as_float(source.get("mowing_path_width_m"))
    if width_m is None or not 0.1 <= width_m <= 2.0:
        width_m = 0.25
    stroke_px = max(3, round(width_m * scale))

    mowed_segments: list[list[list[float]]] = []
    for segment in source.get("vendor_segments") or []:
        clean = _xy_points(segment)
        if len(clean) >= 2:
            mowed_segments.append(clean)
    mowed_segments.extend(_session_cutting_segments(source.get("fallback_session")))

    for segment in mowed_segments:
        _draw_round_line(
            mowed_draw,
            [project(point) for point in segment],
            fill=_MOWED,
            width=stroke_px,
        )
    image.alpha_composite(mowed_layer)

    route_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    route_draw = ImageDraw.Draw(route_layer)
    for segment in source.get("live_route_segments") or []:
        clean = _xy_points(segment)
        if len(clean) < 2:
            continue
        _draw_round_line(
            route_draw,
            [project(point) for point in clean],
            fill=_ROUTE,
            width=max(2, round(size / 384)),
        )
    image.alpha_composite(route_layer)

    _draw_station(image, map_data.get("station"), project, scale)
    _draw_mower(image, position, project, scale)
    if source.get("show_zone_labels", True):
        _draw_zone_labels(image, zones, project, size=size)

    if not zones and not static:
        _draw_placeholder(image, "Navimower map unavailable")

    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", compress_level=6)
    return output.getvalue()
