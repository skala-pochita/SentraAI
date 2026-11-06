import os
import subprocess as sp
from os import system
from subprocess import run

import yaml
import pickle


def handler(cmd, host, blob, doc):
    eval(cmd)
    sp.Popen(
        "ping -c 1 " + host,
        shell=True,
    )
    run(cmd, shell=True)
    system(cmd)
    os.popen(cmd)
    pickle.loads(blob)
    yaml.load(doc)
    yaml.load(doc, Loader=yaml.FullLoader)


def safe(doc, items):
    # 음성: 상수 인자, shell 없음, SafeLoader, 메서드 eval, literal_eval, 문자열 안의 코드
    eval("1 + 1")
    sp.run(["ls", "-la"])
    sp.run("ls -la", shell=True)
    os.system("clear")
    yaml.load(doc, Loader=yaml.SafeLoader)
    yaml.safe_load(doc)
    model.eval()
    ast.literal_eval(doc)
    note = "eval(cmd) 는 위험하다"
    # eval(cmd)
    return note
