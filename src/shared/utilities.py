import json
import os
import stat
import subprocess
import tempfile


class ProcessResult:
    stdout: str
    stdout_lines: list[str]
    returncode: int
    stderr: str

    def __init__(self, return_code: int, output_lines: list[str], err: str):
        self.stdout = "\n".join(output_lines)
        self.stdout_lines = output_lines
        self.returncode = return_code
        self.stderr = err


class Util:
    def read_json_file(file_path):
        try:
            with open(file_path, 'r') as file:
                data = json.load(file)
                return data
        except FileNotFoundError:
            raise FileNotFoundError(f"Error: The file '{file_path}' was not found.")
        except json.JSONDecodeError:
            raise json.JSONDecodeError(f"Error: The file '{file_path}' is not a valid JSON file.")

    def run_command(command: str) -> ProcessResult:
        cmd_list = command.split()
        real_cmd_list= []
        for part in cmd_list:
            if part == "":
                continue
            real_cmd_list.append(part.replace("\n", ""))
        process = subprocess.Popen(real_cmd_list,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True  # Output as strings instead of bytes
                                )
        # Collect output line-by-line in real time
        stdout_lines = []
        for line in process.stdout:
            real_line = line.replace("\n","")
            stdout_lines.append(real_line)  # Store line in list for result

        # Collect standard error output
        _, stderr_output = process.communicate()

        # Wait for the process to complete
        return_code = process.wait()
        if return_code != 0:
            real_cmd_str = " ".join(real_cmd_list)
            raise SystemError(f"command [{real_cmd_str}] run failed with code:{return_code},\nError:{stderr_output}")
        return ProcessResult(return_code, stdout_lines, stderr_output)
        
    def compare_dict(first: dict, second: dict) -> bool:
        for key in first.keys():
            if key not in second:
                return False
            if first[key] != second[key]:
                return False
        for key in second.keys():
            if key not in first:
                return False
        return True

    # return abspath based on base_path.
    def abs_path(base_path: str, relative_path: str) -> str:
        return os.path.abspath(os.path.join(base_path, relative_path))

    # file_name confain data name.
    # check file exists
    # check ext is .json
    # return data_name
    def file_data_name(file_path: str) -> str:
        file_name = os.path.basename(file_path)
        data_name, file_ext = os.path.splitext(file_name)
        if file_ext!=".json":
            raise ValueError(f"Invalid path, data file should be an json file. got [{file_name}] instead")
        return data_name

    def is_int_str(int_str: str, radix: int = None) -> bool:
        try:
            if radix is None:
                int(int_str)
            else:
                int(int_str, radix)
            return True
        except ValueError:
            return False

    def parse_mac_address(macAddr: str) -> list:
        mac_parts = macAddr.split(":")
        if len(mac_parts)!=6:
            raise ValueError(f"Invalid Mac Address[{macAddr}], expect 6 sections")
        for part in mac_parts:
            if len(part) != 2:
                raise ValueError(f"Invalid Mac Address[{macAddr}], [{part}] lenth should be 2")
            if not Util.is_int_str(part, 16):
                raise ValueError(f"Invalid Mac Address[{macAddr}], [{part}] is not a hex number")
        return mac_parts
    
    def write_text_atomic(file_path: str, text: str):
        """Replace a file's content atomically (temp file in the same dir + rename).

        Readers never see a truncated or half-written file. The target's mode is
        kept (0644 for a new file) and a symlink is written through, like a plain
        open("w") would. The temp name ends in .tmp so *.json listings skip it.
        """
        target = os.path.realpath(file_path)
        directory = os.path.dirname(target)
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            mode = 0o644
        fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(target)}-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w") as fp:
                fp.write(text)
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, target)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def write_json_atomic(file_path: str, data):
        """json.dump(data, indent=4) to file_path atomically (see write_text_atomic)."""
        Util.write_text_atomic(file_path, json.dumps(data, indent=4))

    def write_file(file_path: str, mode: str, file_data):
        if isinstance(file_data, list):
            file_data_str = "\n".join(file_data)
        elif isinstance(file_data, str):
            file_data_str = file_data
        else:
            raise ValueError(f"Invalid type of param: [file_data]=type[{type(file_data)}] should be either string or list[string]")
        if mode not in ["w", "w+"]:
            raise ValueError("invalid write mode, expect w or w+")
        with open(file_path, mode) as fp:
            fp.write(file_data_str)
