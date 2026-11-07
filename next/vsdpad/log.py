"""Logging to <data>/vsdpad.log (rotating, safe on Windows) and to the console when there is one."""
import logging, logging.handlers, os, sys

LOG = logging.getLogger("vsdpad")


def setup(data_dir, verbose=False):
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOG.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S")
    os.makedirs(data_dir, exist_ok=True)
    # RotatingFileHandler closes its stream before renaming, so rotation works on Windows too.
    fh = logging.handlers.RotatingFileHandler(os.path.join(data_dir, "vsdpad.log"), maxBytes=500_000,
                                              backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    LOG.addHandler(fh)
    if sys.stderr is not None:  # pythonw has no console
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        LOG.addHandler(sh)
    return LOG
