"""生成 ctcloud 渠道的 gRPC proto stub。

用法：
    python scripts/generate_ctcloud_proto.py

前置：pip install grpcio-tools
输出：deeptutor/partners/proto_gen/useragent_chat_pb2.py 与 useragent_chat_pb2_grpc.py
      （以及 mypy 用的 .pyi）
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "deeptutor" / "partners" / "proto_gen"
PROTO_FILE = OUT_DIR / "useragent_chat.proto"


def main() -> None:
    if not PROTO_FILE.is_file():
        raise SystemExit(f"proto 文件不存在：{PROTO_FILE}")

    try:
        from grpc_tools import protoc
    except ImportError:
        raise SystemExit("缺少 grpcio-tools，请先安装：pip install grpcio-tools")

    args = [
        "protoc",
        f"-I{OUT_DIR}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        f"--pyi_out={OUT_DIR}",
        str(PROTO_FILE.name),
    ]
    rc = protoc.main(args)
    if rc != 0:
        raise SystemExit(f"protoc 失败（rc={rc}）")

    # grpc_tools 生成的是同目录相对导入（import useragent_chat_pb2 as ...），
    # 在包内不可用——改写为绝对包导入，保证 deeptutor.partners.proto_gen
    # 作为包被导入时正常。
    grpc_stub = OUT_DIR / "useragent_chat_pb2_grpc.py"
    text = grpc_stub.read_text("utf-8")
    text = text.replace(
        "import useragent_chat_pb2 as useragent__chat__pb2",
        "from deeptutor.partners.proto_gen import useragent_chat_pb2 as useragent__chat__pb2",
    )
    grpc_stub.write_text(text, "utf-8")

    print(f"OK: 生成 {OUT_DIR / 'useragent_chat_pb2.py'} 等")


if __name__ == "__main__":
    main()
