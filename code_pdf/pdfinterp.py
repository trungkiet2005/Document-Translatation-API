import logging
from typing import Any, Dict, Optional, Sequence, Tuple, cast
import numpy as np

from pdfminer import settings
from pdfminer.pdfcolor import PREDEFINED_COLORSPACE, PDFColorSpace
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfinterp import (
    PDFPageInterpreter,
    PDFResourceManager,
    PDFContentParser,
    PDFInterpreterError,
    Color,
    PDFStackT,
    LITERAL_FORM,
    LITERAL_IMAGE,
)
from pdfminer.pdffont import PDFFont
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import (
    PDFObjRef,
    dict_value,
    list_value,
    resolve1,
    stream_value,
)
from pdfminer.psexceptions import PSEOF
from pdfminer.psparser import (
    PSKeyword,
    keyword_name,
    literal_name,
)
from pdfminer.utils import (
    MATRIX_IDENTITY,
    Matrix,
    Rect,
    mult_matrix,
    apply_matrix_pt,
)

log = logging.getLogger(__name__)


def safe_float(o: Any) -> Optional[float]:
    try:
        return float(o)
    except (TypeError, ValueError):
        return None


class PDFPageInterpreterEx(PDFPageInterpreter):
    """Processor for the content of a PDF page

    Reference: PDF Reference, Appendix A, Operator Summary
    """

    def __init__(
        self, rsrcmgr: PDFResourceManager, device: PDFDevice, obj_patch
    ) -> None:
        self.rsrcmgr = rsrcmgr
        self.device = device
        self.obj_patch = obj_patch

    def dup(self) -> "PDFPageInterpreterEx":
        return self.__class__(self.rsrcmgr, self.device, self.obj_patch)

    def init_resources(self, resources: Dict[object, object]) -> None:
        # Ghi đè để thiết lập fontid và descent
        """Prepare the fonts and XObjects listed in the Resource attribute."""
        self.resources = resources
        self.fontmap: Dict[object, PDFFont] = {}
        self.fontid: Dict[PDFFont, object] = {}
        self.xobjmap = {}
        self.csmap: Dict[str, PDFColorSpace] = PREDEFINED_COLORSPACE.copy()
        if not resources:
            return

        def get_colorspace(spec: object) -> Optional[PDFColorSpace]:
            if isinstance(spec, list):
                name = literal_name(spec[0])
            else:
                name = literal_name(spec)
            if name == "ICCBased" and isinstance(spec, list) and len(spec) >= 2:
                return PDFColorSpace(name, stream_value(spec[1])["N"])
            elif name == "DeviceN" and isinstance(spec, list) and len(spec) >= 2:
                return PDFColorSpace(name, len(list_value(spec[1])))
            else:
                return PREDEFINED_COLORSPACE.get(name)

        for k, v in dict_value(resources).items():
            # log.debug("Resource: %r: %r", k, v)
            if k == "Font":
                for fontid, spec in dict_value(v).items():
                    objid = None
                    if isinstance(spec, PDFObjRef):
                        objid = spec.objid
                    spec = dict_value(spec)
                    self.fontmap[fontid] = self.rsrcmgr.get_font(objid, spec)
                    self.fontmap[fontid].descent = 0  # hack sửa giá trị descent
                    self.fontid[self.fontmap[fontid]] = fontid
            elif k == "ColorSpace":
                for csid, spec in dict_value(v).items():
                    colorspace = get_colorspace(resolve1(spec))
                    if colorspace is not None:
                        self.csmap[csid] = colorspace
            elif k == "ProcSet":
                self.rsrcmgr.get_procset(list_value(v))
            elif k == "XObject":
                for xobjid, xobjstrm in dict_value(v).items():
                    self.xobjmap[xobjid] = xobjstrm

    def do_S(self) -> None:
        # Ghi đè để lọc đường kẻ không phải công thức
        """Stroke path"""

        def is_black(color: Color) -> bool:
            if isinstance(color, Tuple):
                return sum(color) == 0
            else:
                return color == 0

        if (
            len(self.curpath) == 2
            and self.curpath[0][0] == "m"
            and self.curpath[1][0] == "l"
            and apply_matrix_pt(self.ctm, self.curpath[0][-2:])[1]
            == apply_matrix_pt(self.ctm, self.curpath[1][-2:])[1]
            and is_black(self.graphicstate.scolor)
        ):  # Đường thẳng độc lập, ngang, màu đen
            # print(apply_matrix_pt(self.ctm,self.curpath[0][-2:]),apply_matrix_pt(self.ctm,self.curpath[1][-2:]),self.graphicstate.scolor)
            self.device.paint_path(self.graphicstate, True, False, False, self.curpath)
            self.curpath = []
            return "n"
        else:
            self.curpath = []

    ############################################################
    # Ghi đè để lọc đường kẻ không phải công thức (F/B)
    def do_f(self) -> None:
        """Fill path using nonzero winding number rule"""
        # self.device.paint_path(self.graphicstate, False, True, False, self.curpath)
        self.curpath = []

    def do_F(self) -> None:
        """Fill path using nonzero winding number rule (obsolete)"""

    def do_f_a(self) -> None:
        """Fill path using even-odd rule"""
        # self.device.paint_path(self.graphicstate, False, True, True, self.curpath)
        self.curpath = []

    def do_B(self) -> None:
        """Fill and stroke path using nonzero winding number rule"""
        # self.device.paint_path(self.graphicstate, True, True, False, self.curpath)
        self.curpath = []

    def do_B_a(self) -> None:
        """Fill and stroke path using even-odd rule"""
        # self.device.paint_path(self.graphicstate, True, True, True, self.curpath)
        self.curpath = []

    ############################################################
    # Ghi đè để trả về tham số gọi (SCN)
    def do_SCN(self) -> None:
        """Set color for stroking operations."""
        if self.scs:
            n = self.scs.ncomponents
        else:
            if settings.STRICT:
                raise PDFInterpreterError("No colorspace specified!")
            n = 1
        args = self.pop(n)
        self.graphicstate.scolor = cast(Color, args)
        return args

    def do_scn(self) -> None:
        """Set color for nonstroking operations"""
        if self.ncs:
            n = self.ncs.ncomponents
        else:
            if settings.STRICT:
                raise PDFInterpreterError("No colorspace specified!")
            n = 1
        args = self.pop(n)
        self.graphicstate.ncolor = cast(Color, args)
        return args

    def do_SC(self) -> None:
        """Set color for stroking operations"""
        return self.do_SCN()

    def do_sc(self) -> None:
        """Set color for nonstroking operations"""
        return self.do_scn()

    def do_Do(self, xobjid_arg: PDFStackT) -> None:
        # Ghi đè để thiết lập obj_patch của xobj
        """Invoke named XObject"""
        xobjid = literal_name(xobjid_arg)
        try:
            xobj = stream_value(self.xobjmap[xobjid])
        except KeyError:
            if settings.STRICT:
                raise PDFInterpreterError("Undefined xobject id: %r" % xobjid)
            return
        # log.debug("Processing xobj: %r", xobj)
        subtype = xobj.get("Subtype")
        if subtype is LITERAL_FORM and "BBox" in xobj:
            interpreter = self.dup()
            bbox = cast(Rect, list_value(xobj["BBox"]))
            matrix = cast(Matrix, list_value(xobj.get("Matrix", MATRIX_IDENTITY)))
            # Theo tài liệu tham khảo PDF 1.7 phần 4.9.1, XObjects trong
            # các tệp PDF cũ hơn (trước v1.2) sử dụng mục Resources của trang
            # thay vì có mục Resources riêng.
            xobjres = xobj.get("Resources")
            if xobjres:
                resources = dict_value(xobjres)
            else:
                resources = self.resources.copy()
            self.device.begin_figure(xobjid, bbox, matrix)
            ctm = mult_matrix(matrix, self.ctm)
            ops_base = interpreter.render_contents(
                resources,
                [xobj],
                ctm=ctm,
            )
            self.ncs = interpreter.ncs
            self.scs = interpreter.scs
            try:  # Đôi khi font form không thể thêm vào khiến mã bị lỗi
                self.device.fontid = interpreter.fontid
                self.device.fontmap = interpreter.fontmap
                ops_new = self.device.end_figure(xobjid)
                ctm_inv = np.linalg.inv(np.array(ctm[:4]).reshape(2, 2))
                np_version = np.__version__
                if np_version.split(".")[0] >= "2":
                    pos_inv = -np.asmatrix(ctm[4:]) * ctm_inv
                else:
                    pos_inv = -np.mat(ctm[4:]) * ctm_inv
                a, b, c, d = ctm_inv.reshape(4).tolist()
                e, f = pos_inv.tolist()[0]
                self.obj_patch[self.xobjmap[xobjid].objid] = (
                    f"q {ops_base}Q {a} {b} {c} {d} {e} {f} cm {ops_new}"
                )
            except Exception:
                pass
        elif subtype is LITERAL_IMAGE and "Width" in xobj and "Height" in xobj:
            self.device.begin_figure(xobjid, (0, 0, 1, 1), MATRIX_IDENTITY)
            self.device.render_image(xobjid, xobj)
            self.device.end_figure(xobjid)
        else:
            # Loại xobject không được hỗ trợ.
            pass

    def process_page(self, page: PDFPage) -> None:
        # Ghi đè để thiết lập obj_patch của trang
        # log.debug("Processing page: %r", page)
        # print(page.mediabox,page.cropbox)
        # (x0, y0, x1, y1) = page.mediabox
        (x0, y0, x1, y1) = page.cropbox
        if page.rotate == 90:
            ctm = (0, -1, 1, 0, -y0, x1)
        elif page.rotate == 180:
            ctm = (-1, 0, 0, -1, x1, y1)
        elif page.rotate == 270:
            ctm = (0, 1, -1, 0, y1, -x0)
        else:
            ctm = (1, 0, 0, 1, -x0, -y0)
        self.device.begin_page(page, ctm)
        ops_base = self.render_contents(page.resources, page.contents, ctm=ctm)
        self.device.fontid = self.fontid
        self.device.fontmap = self.fontmap
        ops_new = self.device.end_page(page)
        # Khi render ở trên, dùng cropbox để trừ đi độ lệch trang để có tọa độ thực; khi xuất ở đây, cần dùng cm để thêm độ lệch trang vào lại
        self.obj_patch[page.page_xref] = (
            f"q {ops_base}Q 1 0 0 1 {x0} {y0} cm {ops_new}"  # ops_base có thể chứa hình, cần để văn bản ops_new hiển thị trên cùng, sử dụng q/Q để đặt lại ma trận vị trí
        )
        for obj in page.contents:
            self.obj_patch[obj.objid] = ""

    def render_contents(
        self,
        resources: Dict[object, object],
        streams: Sequence[object],
        ctm: Matrix = MATRIX_IDENTITY,
    ) -> None:
        # Ghi đè để trả về luồng lệnh
        """Render the content streams.

        This method may be called recursively.
        """
        # log.debug(
        #     "render_contents: resources=%r, streams=%r, ctm=%r",
        #     resources,
        #     streams,
        #     ctm,
        # )
        self.init_resources(resources)
        self.init_state(ctm)
        return self.execute(list_value(streams))

    def execute(self, streams: Sequence[object]) -> None:
        # Ghi đè để trả về luồng lệnh
        ops = ""
        try:
            parser = PDFContentParser(streams)
        except PSEOF:
            # Trang trống
            return
        while True:
            try:
                (_, obj) = parser.nextobject()
            except PSEOF:
                break
            if isinstance(obj, PSKeyword):
                name = keyword_name(obj)
                method = "do_%s" % name.replace("*", "_a").replace('"', "_w").replace(
                    "'",
                    "_q",
                )
                if hasattr(self, method):
                    func = getattr(self, method)
                    nargs = func.__code__.co_argcount - 1
                    if nargs:
                        args = self.pop(nargs)
                        # log.debug("exec: %s %r", name, args)
                        if len(args) == nargs:
                            func(*args)
                            if not (
                                name[0] == "T"
                                or name in ['"', "'", "EI", "MP", "DP", "BMC", "BDC"]
                            ):  # Lọc các lệnh văn bản dòng T, vì tham số của EI là obj nên cũng cần lọc (chỉ sử dụng trong một số tài liệu vẽ đường ngang), lọc các lệnh marked
                                p = " ".join(
                                    [
                                        (
                                            f"{x:f}"
                                            if isinstance(x, float)
                                            else str(x).replace("'", "")
                                        )
                                        for x in args
                                    ]
                                )
                                ops += f"{p} {name} "
                    else:
                        # log.debug("exec: %s", name)
                        targs = func()
                        if targs is None:
                            targs = []
                        if not (name[0] == "T" or name in ["BI", "ID", "EMC"]):
                            p = " ".join(
                                [
                                    (
                                        f"{x:f}"
                                        if isinstance(x, float)
                                        else str(x).replace("'", "")
                                    )
                                    for x in targs
                                ]
                            )
                            ops += f"{p} {name} "
                elif settings.STRICT:
                    error_msg = "Unknown operator: %r" % name
                    raise PDFInterpreterError(error_msg)
            else:
                self.push(obj)
        # print('REV DATA',ops)
        return ops
