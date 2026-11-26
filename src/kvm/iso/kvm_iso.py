#!/usr/bin/env python3

#########################################################################################
# Kvm ISO Utility
#
# Manage read-only installation media (.iso) that can be attached to a VM as a cdrom.
# Unlike KvmImage, an ISO is never used as a disk or as a backing file.
#########################################################################################

import argparse
import hashlib
import logging
import os
import time
import urllib.request

from shared.utilities import Util
from shared.logger import Log


class KvmIso:
    class Keyword:
        IsoPath = "isoPath"
        IsoSource = "isoSource"
        DownloadLink = "downloadLink"
        Sha256 = "sha256"

        class Source:
            Remote = "remote"
            Upload = "upload"

            @staticmethod
            def list():
                return [
                    KvmIso.Keyword.Source.Remote,
                    KvmIso.Keyword.Source.Upload,
                ]

    Extension = ".iso"
    HashChunkSize = 4 * 1024 * 1024

    @staticmethod
    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(description="KVM ISO Operations")
        parser.add_argument("--path", type=str, help="Kvm ISO Data Path", required=True)
        return parser.parse_args()

    @staticmethod
    def is_sha256(value) -> bool:
        if not isinstance(value, str) or len(value) != 64:
            return False
        try:
            int(value, 16)
        except ValueError:
            return False
        return True

    @staticmethod
    def file_sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(KvmIso.HashChunkSize), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def __init__(self, data_path: str, logger: logging.Logger, progress_callback=None):
        """progress_callback(tmp_path, current_size, total_size) — called during remote download."""
        self.log = logger
        self.progress_callback = progress_callback
        self.DataPath = data_path
        if self.DataPath is None:
            self.DataPath = KvmIso.parse_args().path
        if not os.path.exists(self.DataPath):
            raise ValueError(f"Invalid path does not exists.[{self.DataPath}]")
        self.IsoName = Util.file_data_name(self.DataPath)
        self.IsoData = Util.read_json_file(self.DataPath)
        self.Validate()

    def Validate(self):
        if not isinstance(self.IsoData, dict):
            raise ValueError("Invalid iso data, not dict")
        iso_path = self.IsoData.get(self.Keyword.IsoPath, None)
        if iso_path is None:
            raise ValueError(f"Missing field [{self.Keyword.IsoPath}] in ISO Data")
        if not os.path.isabs(iso_path):
            iso_path = Util.abs_path(os.path.dirname(self.DataPath), iso_path)
            self.IsoData[self.Keyword.IsoPath] = iso_path
        if not iso_path.endswith(self.Extension):
            raise ValueError(f"Invalid value, [{self.Keyword.IsoPath}]=[{iso_path}], expect extension [{self.Extension}]")
        iso_source = self.IsoData.get(self.Keyword.IsoSource, None)
        if iso_source not in self.Keyword.Source.list():
            raise ValueError(f"Invalid [{self.Keyword.IsoSource}]=[{iso_source}], expect [{self.Keyword.Source.list()}]")
        if iso_source == self.Keyword.Source.Remote and not self.IsoData.get(self.Keyword.DownloadLink):
            raise ValueError(f"Invalid data, missing field=[{self.Keyword.DownloadLink}]")
        sha256 = self.IsoData.get(self.Keyword.Sha256, None)
        if sha256 is not None:
            if not self.is_sha256(sha256):
                raise ValueError(f"Invalid [{self.Keyword.Sha256}], expect 64 hex characters")
            self.IsoData[self.Keyword.Sha256] = sha256.lower()

    def IsoPath(self) -> str:
        return self.IsoData[self.Keyword.IsoPath]

    def Create(self):
        iso_path = self.IsoPath()
        if os.path.exists(iso_path):
            self.log.info(f"ISO already exists. [{iso_path}]")
            return
        if self.IsoData[self.Keyword.IsoSource] == self.Keyword.Source.Upload:
            raise ValueError(f"ISO [{self.IsoName}] has isoSource=upload but file does not exist [{iso_path}]")
        self.Download()

    def Download(self):
        iso_path = self.IsoPath()
        os.makedirs(os.path.dirname(iso_path), exist_ok=True)
        download_link = self.IsoData[self.Keyword.DownloadLink]
        tmp_path = iso_path + ".tmp"
        self.log.info(f"Download ISO [{iso_path}] from [{download_link}] to temp [{tmp_path}]")

        last_cb_time = [0.0]

        def reporthook(blocks, block_size, total_size):
            if self.progress_callback is None:
                return
            now = time.time()
            if now - last_cb_time[0] >= 1.0:
                last_cb_time[0] = now
                self.progress_callback(tmp_path, blocks * block_size, total_size)

        try:
            urllib.request.urlretrieve(download_link, tmp_path, reporthook)
            self.VerifySha256(tmp_path)
            os.rename(tmp_path, iso_path)
            self.log.info(f"Download complete, renamed [{tmp_path}] -> [{iso_path}]")
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                self.log.info(f"Download failed, removed temp file [{tmp_path}]")
            raise

    def VerifySha256(self, path: str):
        expected = self.IsoData.get(self.Keyword.Sha256, None)
        if expected is None:
            return
        actual = self.file_sha256(path)
        if actual != expected:
            raise ValueError(f"sha256 mismatch for ISO [{self.IsoName}]: expect [{expected}], got [{actual}]")
        self.log.info(f"sha256 verified for ISO [{self.IsoName}]")

    def cdrom_cmd(self) -> str:
        return f"path={self.IsoPath()},device=cdrom"


if __name__ == "__main__":
    logger = Log.get_logger("KvmIso")
    logger.info("Create Kvm ISO")
    iso = KvmIso(None, logger)
    iso.Create()
