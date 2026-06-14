#!/usr/bin/env python3

#########################################################################################
# Kvm Image Utility
#
# Create image for local Kvm usage.
#########################################################################################

import argparse
import json
import logging
import os
import time
import urllib.request

from shared.utilities import Util
from shared.logger import Log

class KvmImage:
    class Keyword:
        ImageFormat = "imageFormat"
        ImageSource = "imageSource"
        ImagePath = "imagePath"
        DownloadLink = "downloadLink"
        SizeInGB   = "sizeInGB"
        BaseImagePath = "baseImagePath"
        BaseImageFormat = "baseImageFormat"

        class Source:
            Remote = "remote"
            Local = "local"

            @staticmethod
            def list():
                return [
                    KvmImage.Keyword.Source.Remote,
                    KvmImage.Keyword.Source.Local
                ]

        class Formats:
            QCOW2 = "qcow2"
            IMG = "img"

            @staticmethod
            def list():
                return [
                    KvmImage.Keyword.Formats.QCOW2, 
                    KvmImage.Keyword.Formats.IMG
                ]

    @staticmethod
    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(description=f"KVM Image Operations")
        parser.add_argument("--path", type=str, help=f"Kvm Image Data Path for Image Creation", required=True)
        args = parser.parse_args()
        return args

    @staticmethod
    def ImageFormatCmd(image_format):
        if image_format == KvmImage.Keyword.Formats.IMG:
            return "raw"
        elif image_format == KvmImage.Keyword.Formats.QCOW2:
            return "qcow2"

    def __init__(self, data_path:str, logger: logging.Logger, progress_callback=None):
        """progress_callback(tmp_path, current_size, total_size) — called during remote download."""
        self.log = logger
        self.progress_callback = progress_callback
        self.DataPath = data_path
        if self.DataPath is None:
            args = KvmImage.parse_args()
            self.DataPath = args.path
        if not os.path.exists(self.DataPath):
            raise ValueError(f"Invalid path does not exists.[{self.DataPath}]")

        self.ImagName = Util.file_data_name(self.DataPath)
        self.ImageData = Util.read_json_file(self.DataPath)
        self.Validate()
        
    def Validate(self):
        if not isinstance(self.ImageData, dict):
            raise ValueError(f"Invalid image data, not dict")
        image_path = self.ImageData.get(self.Keyword.ImagePath, None)
        if image_path is None:
            raise ValueError(f"Missing field [{self.Keyword.ImagePath}] in Image Data")
        if not os.path.isabs(image_path):
            self.log.info(f"found relative path [{self.Keyword.ImagePath}]=[{image_path}]")
            image_path = Util.abs_path(os.path.dirname(self.DataPath), image_path)
            self.log.info(f"Update [{self.Keyword.ImagePath}]=[{image_path}]")
            self.ImageData[self.Keyword.ImagePath] = image_path
        image_file_name = os.path.basename(image_path)
        image_name, file_ext = os.path.splitext(image_file_name)
        if file_ext == "":
            raise ValueError(f"Invalid value, [{self.Keyword.ImagePath}]=[{image_path}], expect file extension [{self.Keyword.Formats.list()}]")
        image_format = self.ImageData.get(self.Keyword.ImageFormat, None)
        if image_format is None:
            raise ValueError(f"Missing field [{self.Keyword.ImageFormat}] to specify image format")
        if image_format not in self.Keyword.Formats.list():
            raise ValueError(f"Invalid [{self.Keyword.ImageFormat}]=[{image_format}], expect [{self.Keyword.Formats.list()}]")
        image_source = self.ImageData.get(self.Keyword.ImageSource, None)
        if image_source is None:
            raise ValueError(f"Missing field [{self.Keyword.ImageSource}] to specify image source")
        if image_source not in self.Keyword.Source.list():
            raise ValueError(f"Invalid [{self.Keyword.ImageSource}]=[{image_source}], expect [{self.Keyword.Source.list()}]")
        if image_source == self.Keyword.Source.Remote:
            download_link = self.ImageData.get(self.Keyword.DownloadLink, None)
            if download_link is None:
                raise ValueError(f"Invalid data, missing field=[{self.Keyword.DownloadLink}]")
        elif image_source == self.Keyword.Source.Local:
            image_size = self.ImageData.get(self.Keyword.SizeInGB, None)
            if image_size is not None and not isinstance(image_size, int):
                raise ValueError(f"Invalid value, [{self.Keyword.SizeInGB}]=image_size, expect int")
            base_image_path = self.ImageData.get(self.Keyword.BaseImagePath, None)
            if base_image_path is not None:
                if not os.path.isabs(base_image_path):
                    self.log.info(f"found relative path [{self.Keyword.BaseImagePath}]=[{base_image_path}]")
                    base_image_path = Util.abs_path(os.path.dirname(self.DataPath), base_image_path)
                    self.log.info(f"Update [{self.Keyword.BaseImagePath}]=[{base_image_path}]")
                    self.ImageData[self.Keyword.BaseImagePath] = base_image_path
                if not os.path.exists(base_image_path):
                    raise ValueError(f"Invalid value [{self.Keyword.BaseImagePath}] does not exists. [{base_image_path}]")
                base_image_format = self.ImageData.get(self.Keyword.BaseImageFormat, None)
                if base_image_format is None:
                    raise ValueError(f"Missing value [{self.Keyword.BaseImageFormat}]")
                if base_image_format not in self.Keyword.Formats.list():
                    raise ValueError(f"Invalid value [{self.Keyword.BaseImageFormat}]=[{base_image_format}], expect [{self.Keyword.Formats.list()}]")
                
    def Create(self):
        if os.path.exists(self.ImageData[self.Keyword.ImagePath]):
            self.log.info(f"Image already exists. [{self.ImageData[self.Keyword.ImagePath]}]")
            return
        self.DownloadImage()
        self.BuildImage()

    def DownloadImage(self):
        if self.ImageData[self.Keyword.ImageSource] != self.Keyword.Source.Remote:
            return
        image_path = self.ImageData[self.Keyword.ImagePath]
        image_dir = os.path.dirname(os.path.abspath(image_path))
        if not os.path.exists(image_dir):
            self.log.info(f"Image path dir [{image_dir}] does not exists, make one")
            cmd = f"mkdir -p {image_dir}"
            Util.run_command(cmd)
        download_link = self.ImageData[self.Keyword.DownloadLink]
        tmp_path = image_path + ".tmp"
        self.log.info(f"Download image [{image_path}] from [{download_link}] to temp [{tmp_path}]")

        last_cb_time = [0.0]  # mutable container for closure

        def reporthook(blocks, block_size, total_size):
            current_size = blocks * block_size
            if self.progress_callback is not None:
                now = time.time()
                if now - last_cb_time[0] >= 1.0:
                    last_cb_time[0] = now
                    self.progress_callback(tmp_path, current_size, total_size)

        try:
            urllib.request.urlretrieve(download_link, tmp_path, reporthook)
            os.rename(tmp_path, image_path)
            self.log.info(f"Download complete, renamed [{tmp_path}] -> [{image_path}]")
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                self.log.info(f"Download failed, removed temp file [{tmp_path}]")
            raise

    def _query_virtual_size_gb(self, image_path: str) -> int:
        """Query virtual disk size in GB from an existing image file (round up)."""
        cmd = f"qemu-img info --output=json {image_path}"
        result = Util.run_command(cmd)
        info = json.loads(result.stdout)
        virtual_size_bytes = info.get("virtual-size", 0)
        return (virtual_size_bytes + 1024**3 - 1) // 1024**3

    def _save_data(self):
        """Persist current ImageData back to the JSON data file.

        Paths are converted to relative (based on the data file's directory)
        so the JSON remains portable across filesystem moves.
        """
        data_dir = os.path.dirname(self.DataPath)
        save_data = dict(self.ImageData)
        for key in (self.Keyword.ImagePath, self.Keyword.BaseImagePath):
            if save_data.get(key):
                try:
                    save_data[key] = os.path.relpath(save_data[key], data_dir)
                except ValueError:
                    pass
        with open(self.DataPath, "w") as f:
            json.dump(save_data, f, indent=4)

    def BuildImage(self):
        if self.ImageData[self.Keyword.ImageSource] != self.Keyword.Source.Local:
            return
        image_path = self.ImageData[self.Keyword.ImagePath]
        image_format = self.ImageData[self.Keyword.ImageFormat]
        cmd = f"qemu-img create -f {KvmImage.ImageFormatCmd(image_format)}"
        base_image_path = self.ImageData.get(self.Keyword.BaseImagePath, None)
        if base_image_path is not None:
            self.log.info(f"Create image {image_path} from {base_image_path}")
            base_image_format = self.ImageData[self.Keyword.BaseImageFormat]
            cmd = f"{cmd} -b {base_image_path} -F {KvmImage.ImageFormatCmd(base_image_format)}"
        cmd = f"{cmd} {image_path}"
        image_size = self.ImageData.get(self.Keyword.SizeInGB, None)
        if image_size is None and base_image_path is not None:
            image_size = self._query_virtual_size_gb(base_image_path)
            self.ImageData[self.Keyword.SizeInGB] = image_size
            self._save_data()
            self.log.info(f"Auto-fill [{self.Keyword.SizeInGB}]={image_size} from backing file")
        if image_size is not None:
            self.log.info(f"Define image size to {image_size}G")
            cmd = f"{cmd} {image_size}G"
        self.log.info(f"run command [{cmd}]")
        Util.run_command(cmd)

    def ImagePath(self):
        return self.ImageData[self.Keyword.ImagePath]

    def disk_cmd(self) -> str:
        return f"path={self.ImageData[self.Keyword.ImagePath]}"

if __name__ == "__main__":
    logger = Log.get_logger("KvmImage")
    logger.info("Create Kvm Image")
    image = KvmImage(None, logger)
    image.Create()
