# -*- coding: utf-8 -*-
from __future__ import print_function
"""
Abaqus MCP Plugin v4.0 - file IPC bridge.

Bridges Abaqus/CAE kernel to external MCP clients via file-based IPC.
Supports script execution, model/job/ODB queries, and viewport capture.

Usage:
1. File -> Run Script... -> choose this file
2. Run mcp_start() for non-blocking background mode (recommended)
3. Run mcp_loop() for blocking mode
4. Run mcp_stop() to stop
"""

import base64
import io
import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime

__version__ = '4.0.0'

try:
    from abaqus import mdb, session
    ABAQUS_AVAILABLE = True
except ImportError:
    ABAQUS_AVAILABLE = False


def _resolve_mcp_home():
    """Resolve MCP home with explicit override support."""
    env_home = os.environ.get('ABAQUS_MCP_HOME', '').strip()
    if env_home:
        return os.path.abspath(os.path.expanduser(env_home))
    try:
        this_file = os.path.abspath(__file__)
        script_dir = os.path.dirname(this_file)
        if os.path.exists(os.path.join(script_dir, 'stop_mcp.py')):
            return script_dir
    except Exception:
        pass
    return r'E:\Song\2fin-L\abaqus-mcp'


MCP_HOME = _resolve_mcp_home()
COMMANDS_DIR = os.path.join(MCP_HOME, 'commands')
RESULTS_DIR = os.path.join(MCP_HOME, 'results')
SCRIPTS_DIR = os.path.join(MCP_HOME, 'scripts')
SCREENSHOTS_DIR = os.path.join(MCP_HOME, 'screenshots')
STATUS_FILE = os.path.join(MCP_HOME, 'status.json')
STOP_FILE = os.path.join(MCP_HOME, 'stop.flag')
LOG_FILE = os.path.join(MCP_HOME, 'mcp.log')

STALE_COMMAND_AGE = 120.0


def ensure_dirs():
    for d in [COMMANDS_DIR, RESULTS_DIR, SCRIPTS_DIR, SCREENSHOTS_DIR]:
        if not os.path.exists(d):
            os.makedirs(d)


def _log(level, message):
    """Append a log entry to mcp.log (best-effort, never raises)."""
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = '[%s] %s: %s\n' % (ts, level, message)
        with open(LOG_FILE, 'a') as f:
            f.write(line)
    except Exception:
        pass


def write_status(status, message=""):
    """Write status file (Python 2.7 + Windows compatible)."""
    payload = {
        "status": status,
        "message": message,
        "version": __version__,
        "timestamp": time.time(),
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
        "mcp_home": MCP_HOME,
    }
    tmp_file = STATUS_FILE + '.tmp'
    try:
        payload_str = json.dumps(payload, indent=2)
        with open(tmp_file, 'w') as f:
            f.write(payload_str)
        if os.path.exists(STATUS_FILE):
            try:
                os.remove(STATUS_FILE)
            except Exception:
                pass
        try:
            os.rename(tmp_file, STATUS_FILE)
            return
        except Exception:
            pass
        with open(STATUS_FILE, 'w') as f:
            f.write(payload_str)
        try:
            os.remove(tmp_file)
        except Exception:
            pass
    except Exception:
        pass


def _write_json(path, data):
    s = json.dumps(data, indent=2)
    with open(path, 'w') as f:
        f.write(s)


def _background_self_test(timeout=1.5):
    """
    Verify background worker can consume command files and write result files.
    Returns True if ping loopback succeeds.
    """
    test_id = 'bgtest_' + uuid.uuid4().hex[:8]
    cmd_path = os.path.join(COMMANDS_DIR, 'cmd_' + test_id + '.json')
    result_path = os.path.join(RESULTS_DIR, test_id + '.json')
    command = {
        'id': test_id,
        'type': 'ping',
        'timestamp': time.time(),
    }
    try:
        _write_json(cmd_path, command)
    except Exception:
        return False

    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        if os.path.exists(result_path):
            try:
                with open(result_path, 'rb') as f:
                    raw = f.read()
                data = None
                for enc in ['utf-8-sig', 'utf-8', 'gbk', 'latin-1']:
                    try:
                        data = json.loads(raw.decode(enc))
                        break
                    except (UnicodeDecodeError, ValueError):
                        continue
                if data is not None:
                    return bool(data.get('success'))
                return False
            except Exception:
                return False
            finally:
                try:
                    os.remove(result_path)
                except Exception:
                    pass
        time.sleep(0.05)

    # cleanup stale test command/result
    try:
        if os.path.exists(cmd_path):
            os.remove(cmd_path)
    except Exception:
        pass
    try:
        if os.path.exists(result_path):
            os.remove(result_path)
    except Exception:
        pass
    return False


def _cleanup_stale_commands():
    """Remove command files older than STALE_COMMAND_AGE seconds."""
    now = time.time()
    try:
        for name in os.listdir(COMMANDS_DIR):
            if not name.endswith('.json'):
                continue
            fpath = os.path.join(COMMANDS_DIR, name)
            try:
                age = now - os.path.getmtime(fpath)
                if age > STALE_COMMAND_AGE:
                    os.remove(fpath)
                    _log('WARN', 'Removed stale command: ' + name)
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def execute_script(script_content, script_id):
    result = {
        "id": script_id,
        "success": False,
        "output": "",
        "error": None,
        "timestamp": time.time(),
    }
    script_path = os.path.join(SCRIPTS_DIR, 'script_' + script_id + '.py')
    try:
        if isinstance(script_content, bytes):
            raw = script_content
        else:
            raw = script_content.encode('utf-8')
        with open(script_path, 'wb') as f:
            f.write(raw)
    except Exception as e:
        result['error'] = str(e)
        return result

    exec_globals = {'__name__': '__main__', '__file__': script_path}
    try:
        from abaqus import mdb, session
        exec_globals['mdb'] = mdb
        exec_globals['session'] = session
    except Exception:
        pass

    output_lines = []
    exec_globals['print'] = lambda *a, **k: output_lines.append(' '.join(str(x) for x in a))

    try:
        with open(script_path, 'rb') as f:
            raw = f.read()
        script_text = None
        for enc in ['utf-8-sig', 'utf-8', 'gbk', 'latin-1']:
            try:
                script_text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if script_text is None:
            script_text = raw.decode('latin-1')
        exec(compile(script_text, script_path, 'exec'), exec_globals)
        result['success'] = True
        result['output'] = '\n'.join(output_lines)
    except Exception as e:
        result['error'] = str(e)
        result['traceback'] = traceback.format_exc()
    try:
        os.remove(script_path)
    except Exception:
        pass
    return result


def get_model_info():
    info = {'models': [], 'working_directory': os.getcwd()}
    try:
        from abaqus import mdb, session
        for name in mdb.models.keys():
            model_obj = mdb.models[name]
            model_data = {
                'name': name,
                'parts': list(model_obj.parts.keys()) if hasattr(model_obj, 'parts') else [],
                'materials': list(model_obj.materials.keys()) if hasattr(model_obj, 'materials') else [],
                'steps': list(model_obj.steps.keys()) if hasattr(model_obj, 'steps') else [],
                'assemblies': [],
                'loads': list(model_obj.loads.keys()) if hasattr(model_obj, 'loads') else [],
                'bcs': list(model_obj.boundaryConditions.keys()) if hasattr(model_obj, 'boundaryConditions') else [],
                'interactions': list(model_obj.interactions.keys()) if hasattr(model_obj, 'interactions') else [],
            }
            if hasattr(model_obj, 'rootAssembly') and model_obj.rootAssembly:
                ra = model_obj.rootAssembly
                if hasattr(ra, 'instances'):
                    model_data['assemblies'] = list(ra.instances.keys())
            info['models'].append(model_data)
        if hasattr(session, 'viewports'):
            info['current_viewport'] = session.currentViewportName
            info['viewports'] = list(session.viewports.keys())
    except Exception as e:
        info['error'] = str(e)
    return info


def list_jobs():
    """List all jobs in the current Abaqus session with their status."""
    jobs_info = []
    try:
        from abaqus import mdb
        for name in mdb.jobs.keys():
            job = mdb.jobs[name]
            job_data = {'name': name}
            for attr in ('status', 'type', 'model', 'description',
                         'numCpus', 'numDomains', 'memory'):
                try:
                    val = getattr(job, attr, None)
                    if val is not None:
                        job_data[attr] = str(val)
                except Exception:
                    pass
            jobs_info.append(job_data)
    except Exception as e:
        return {'error': str(e), 'jobs': []}
    return {'jobs': jobs_info}


def submit_job(job_name):
    """Submit a job by name."""
    try:
        from abaqus import mdb
        if job_name not in mdb.jobs:
            return {'success': False, 'error': 'Job not found: ' + job_name}
        job = mdb.jobs[job_name]
        job.submit(consistencyChecking=False)
        job.waitForCompletion()
        status = str(getattr(job, 'status', 'UNKNOWN'))
        return {'success': True, 'job': job_name, 'status': status}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def get_odb_info(odb_path):
    """Open an ODB (read-only) and return its metadata."""
    info = {}
    try:
        from odbAccess import openOdb
        odb = openOdb(path=str(odb_path), readOnly=True)
        try:
            info['steps'] = {}
            for step_name in odb.steps.keys():
                step = odb.steps[step_name]
                info['steps'][step_name] = {
                    'number': step.number,
                    'totalTime': step.totalTime,
                    'frames': len(step.frames),
                }
            info['parts'] = list(odb.parts.keys()) if hasattr(odb, 'parts') else []
            info['instances'] = list(odb.rootAssembly.instances.keys()) if hasattr(odb, 'rootAssembly') else []
            if hasattr(odb, 'sectionCategories'):
                info['sectionCategories'] = list(odb.sectionCategories.keys())
        finally:
            odb.close()
        info['success'] = True
    except Exception as e:
        info['success'] = False
        info['error'] = str(e)
    return info


def get_viewport_image(viewport_name=None, width=800, height=600, fmt='PNG'):
    """Capture a viewport image and return it as base64."""
    try:
        from abaqus import session
        vp_name = viewport_name or session.currentViewportName
        if vp_name not in session.viewports:
            return {'success': False, 'error': 'Viewport not found: ' + str(vp_name)}

        img_file = os.path.join(SCREENSHOTS_DIR, 'viewport_' + str(int(time.time())) + '.' + fmt.lower())
        session.printToFile(
            fileName=img_file,
            format=getattr(session, fmt.upper(), session.PNG),
            canvasObjects=(session.viewports[vp_name],)
        )
        if os.path.exists(img_file):
            with open(img_file, 'rb') as f:
                data = base64.b64encode(f.read())
            if isinstance(data, bytes):
                data = data.decode('ascii')
            try:
                os.remove(img_file)
            except Exception:
                pass
            return {'success': True, 'image_base64': data, 'format': fmt.lower()}
        return {'success': False, 'error': 'Image file not created'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

def process_command(command):
    cmd_id = command.get('id', 'unknown')
    cmd_type = command.get('type', 'unknown')
    result = {'id': cmd_id, 'success': False, 'timestamp': time.time()}

    try:
        if cmd_type == 'execute_script':
            result = execute_script(command.get('script', ''), cmd_id)
        elif cmd_type == 'get_model_info':
            result['success'] = True
            result['data'] = get_model_info()
        elif cmd_type == 'list_jobs':
            data = list_jobs()
            result['success'] = 'error' not in data
            result['data'] = data
        elif cmd_type == 'submit_job':
            data = submit_job(command.get('job_name', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'get_odb_info':
            data = get_odb_info(command.get('odb_path', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'get_viewport_image':
            data = get_viewport_image(
                viewport_name=command.get('viewport_name'),
                width=command.get('width', 800),
                height=command.get('height', 600),
                fmt=command.get('format', 'PNG'),
            )
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'get_message_log':
            result['success'] = True
            result['data'] = 'Log not available'
        elif cmd_type == 'ping':
            result['success'] = True
            result['data'] = {'response': 'pong', 'version': __version__}
        elif cmd_type == 'stop':
            result['success'] = True
            result['data'] = 'stopping'
            with open(STOP_FILE, 'w') as f:
                f.write('stop')
        else:
            result['error'] = 'Unknown command: ' + cmd_type
    except Exception as e:
        result['error'] = str(e)
        result['traceback'] = traceback.format_exc()
        _log('ERROR', 'process_command(%s): %s' % (cmd_type, str(e)))

    return result


# ---------------------------------------------------------------------------
# Polling engine
# ---------------------------------------------------------------------------

def _load_command_file(cmd_path, retries=3, delay=0.03):
    """Retry reads briefly to tolerate partially-written command files."""
    for _ in range(retries):
        try:
            with open(cmd_path, 'rb') as f:
                raw = f.read()
            for enc in ['utf-8-sig', 'utf-8', 'gbk', 'latin-1']:
                try:
                    text = raw.decode(enc)
                    return json.loads(text)
                except (UnicodeDecodeError, ValueError):
                    continue
        except Exception:
            time.sleep(delay)
    return None


_mcp_running = False
_mcp_thread = None
_mcp_generation = 0
_mcp_poll_interval = 0.1
_mcp_last_status_time = 0.0
_mcp_commands_processed = 0
_mcp_start_time = 0.0


def poll_once():
    """Process a single command. Returns True if one command was processed."""
    global _mcp_last_status_time, _mcp_commands_processed
    if not _mcp_running:
        return False

    now = time.time()
    if now - _mcp_last_status_time >= 2.0:
        uptime = int(now - _mcp_start_time) if _mcp_start_time else 0
        write_status('running', 'Polling active | cmds=%d uptime=%ds' % (_mcp_commands_processed, uptime))
        _mcp_last_status_time = now

    try:
        cmd_files = [name for name in os.listdir(COMMANDS_DIR) if name.endswith('.json')]
        if not cmd_files:
            return False

        cmd_files.sort()
        cmd_file = cmd_files[0]
        cmd_path = os.path.join(COMMANDS_DIR, cmd_file)

        command = _load_command_file(cmd_path)
        if command is None:
            return False

        cmd_id = command.get('id', 'unknown')
        cmd_type = command.get('type', 'unknown')

        try:
            os.remove(cmd_path)
        except Exception:
            pass

        result = process_command(command)
        _mcp_commands_processed += 1

        result_path = os.path.join(RESULTS_DIR, cmd_id + '.json')
        _write_json(result_path, result)

        if cmd_type != 'ping':
            status = 'OK' if result.get('success') else 'FAIL'
            print('MCP: ' + cmd_type + ' [' + status + ']')
            _log('INFO', '%s [%s] id=%s' % (cmd_type, status, cmd_id))

        return True
    except Exception as e:
        print('MCP: Error: ' + str(e))
        _log('ERROR', 'poll_once: ' + str(e))
        return False


# ---------------------------------------------------------------------------
# Start / stop helpers
# ---------------------------------------------------------------------------

def _set_thread_daemon(thread_obj):
    try:
        thread_obj.daemon = True
    except Exception:
        try:
            thread_obj.setDaemon(True)
        except Exception:
            pass
    return thread_obj


def _thread_is_alive(thread_obj):
    if thread_obj is None:
        return False
    try:
        return thread_obj.is_alive()
    except Exception:
        try:
            return thread_obj.isAlive()
        except Exception:
            return False


def _mcp_thread_loop(generation, poll_interval):
    """Background polling loop used by non-blocking start modes."""
    global _mcp_running, _mcp_thread, _mcp_generation
    last_status_time = 0.0
    cleanup_time = 0.0

    try:
        while _mcp_running and _mcp_generation == generation:
            now = time.time()
            if now - last_status_time >= 2.0:
                uptime = int(now - _mcp_start_time) if _mcp_start_time else 0
                write_status('running', 'Polling active (background) | cmds=%d uptime=%ds' % (_mcp_commands_processed, uptime))
                last_status_time = now

            if now - cleanup_time >= 30.0:
                _cleanup_stale_commands()
                cleanup_time = now

            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except Exception:
                    pass
                _mcp_running = False
                print('MCP: Stopped by stop.flag')
                _log('INFO', 'Stopped by stop.flag')
                break

            poll_once()
            time.sleep(poll_interval)
    except Exception as e:
        err_path = os.path.join(MCP_HOME, 'thread_error.log')
        try:
            with open(err_path, 'w') as f:
                f.write(str(e) + '\n\n')
                f.write(traceback.format_exc())
        except Exception:
            pass
        print('MCP: Background worker error: ' + str(e))
        _log('ERROR', 'Background worker: ' + str(e))
    finally:
        if _mcp_generation == generation:
            _mcp_running = False
            _mcp_thread = None
            write_status('stopped', 'Polling stopped')
            print('MCP: Background loop ended')
            _log('INFO', 'Background loop ended')


def _start_worker(interval=0.1, mode_name='background'):
    global _mcp_running, _mcp_thread, _mcp_generation, _mcp_poll_interval
    global _mcp_commands_processed, _mcp_start_time

    if _thread_is_alive(_mcp_thread):
        print('MCP: Already running')
        return True

    if _mcp_running:
        print('MCP: Recovering from stale running state')
        _mcp_running = False

    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    try:
        _mcp_poll_interval = max(0.02, float(interval))
    except Exception:
        _mcp_poll_interval = 0.1

    _mcp_generation += 1
    generation = _mcp_generation
    _mcp_running = True
    _mcp_commands_processed = 0
    _mcp_start_time = time.time()

    print('MCP: Starting in ' + mode_name + ' worker...')
    print('MCP: Use mcp_stop() or Plug-ins -> MCP -> Stop MCP to stop')
    print('MCP: Abaqus GUI remains responsive!')

    try:
        worker = threading.Thread(target=_mcp_thread_loop, args=(generation, _mcp_poll_interval))
        _set_thread_daemon(worker)
        worker.start()
        _mcp_thread = worker
    except Exception as e:
        _mcp_running = False
        _mcp_thread = None
        write_status('error', 'Background start failed: ' + str(e))
        print('MCP: Failed to start background worker: ' + str(e))
        _log('ERROR', 'Failed to start: ' + str(e))
        return False

    time.sleep(0.05)
    if not _thread_is_alive(_mcp_thread):
        _mcp_running = False
        _mcp_thread = None
        write_status('error', 'Background worker exited during startup')
        print('MCP: Background worker exited during startup')
        return False

    write_status('running', 'Polling active (' + mode_name + ')')
    print('MCP: Background worker started (interval=' + str(_mcp_poll_interval) + 's)')
    _log('INFO', 'Started in ' + mode_name + ' mode')
    return True


def mcp_start(interval=0.1):
    """Start background thread polling (experimental on some Abaqus builds)."""
    global _mcp_running, _mcp_poll_interval

    if _mcp_running:
        print('MCP: Already running')
        return

    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_poll_interval = max(0.02, float(interval))
    ok = _start_worker(interval=interval, mode_name='background')
    if not ok:
        return

    # Validate background mode really processes file IPC.
    if not _background_self_test(timeout=1.5):
        _log('WARN', 'Background mode self-test failed; recommend mcp_loop()')
        print('MCP: Background mode did not pass self-test in this Abaqus session.')
        print('MCP: Recommended stable mode: mcp_loop()')
        print('MCP: You can stop current mode with mcp_stop().')
    else:
        print('MCP: Background mode self-test passed.')


def mcp_start_timer(interval=0.1):
    """Compatibility alias for previous timer mode."""
    _start_worker(interval=interval, mode_name='timer-compatible')


def mcp_stop():
    """Stop mcp_loop() or mcp_start()."""
    global _mcp_running, _mcp_thread, _mcp_generation

    _mcp_running = False
    _mcp_generation += 1

    try:
        with open(STOP_FILE, 'w') as f:
            f.write('stop')
    except Exception:
        pass

    if _thread_is_alive(_mcp_thread):
        try:
            _mcp_thread.join(1.0)
        except Exception:
            pass

    _mcp_thread = None
    write_status('stopped', 'Polling stopped')
    print('MCP: Stop signal sent')
    _log('INFO', 'Stop signal sent')


def mcp_loop(sleep_interval=0.1):
    """Blocking loop that continuously processes MCP commands."""
    global _mcp_running, _mcp_commands_processed, _mcp_start_time
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_running = True
    _mcp_commands_processed = 0
    _mcp_start_time = time.time()

    print('MCP: Listening for commands...')
    print('MCP: To stop, run in PowerShell:')
    print('     echo $null > "$env:USERPROFILE\\.abaqus-mcp\\stop.flag"')
    print('')

    write_status('running', 'Polling active (blocking)')
    _log('INFO', 'Started in blocking mode')
    last_status_time = 0.0
    cleanup_time = 0.0

    try:
        while True:
            now = time.time()
            if now - last_status_time >= 2.0:
                write_status('running', 'Polling active (blocking)')
                last_status_time = now

            if now - cleanup_time >= 30.0:
                _cleanup_stale_commands()
                cleanup_time = now

            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except Exception:
                    pass
                print('MCP: Stopped by stop.flag')
                break

            poll_once()
            time.sleep(max(0.02, float(sleep_interval)))
    except KeyboardInterrupt:
        print('\nMCP: Stopped by Ctrl+C')
    except Exception as e:
        print('MCP: Error: ' + str(e))
        _log('ERROR', 'mcp_loop: ' + str(e))

    write_status('stopped', 'Polling stopped')
    print('MCP: Loop ended')
    _log('INFO', 'Blocking loop ended')


def mcp_coop_loop(sleep_interval=0.1):
    """Cooperative loop: runs in current thread but yields GUI updates."""
    global _mcp_running, _mcp_commands_processed, _mcp_start_time
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_running = True
    _mcp_commands_processed = 0
    _mcp_start_time = time.time()

    print('MCP: Listening for commands... (cooperative mode)')
    print('MCP: To stop, run mcp_stop() or create stop.flag')
    write_status('running', 'Polling active (cooperative)')
    _log('INFO', 'Started in cooperative mode')
    last_status_time = 0.0
    cleanup_time = 0.0

    try:
        while True:
            now = time.time()
            if now - last_status_time >= 2.0:
                write_status('running', 'Polling active (cooperative)')
                last_status_time = now

            if now - cleanup_time >= 30.0:
                _cleanup_stale_commands()
                cleanup_time = now

            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except Exception:
                    pass
                print('MCP: Stopped by stop.flag')
                break

            poll_once()

            try:
                if ABAQUS_AVAILABLE:
                    session.processUpdates()
            except Exception:
                pass

            time.sleep(max(0.02, float(sleep_interval)))
    except KeyboardInterrupt:
        print('\nMCP: Stopped by Ctrl+C')
    except Exception as e:
        print('MCP: Error: ' + str(e))
        _log('ERROR', 'mcp_coop_loop: ' + str(e))

    write_status('stopped', 'Polling stopped')
    print('MCP: Cooperative loop ended')
    _log('INFO', 'Cooperative loop ended')


def mcp_status():
    """Print current MCP status."""
    print('')
    print('=' * 55)
    print('Abaqus MCP Plugin v' + __version__)
    print('=' * 55)
    print('Mode:         File IPC')
    print('Home:         ' + MCP_HOME)
    print('Running:      ' + str(_mcp_running))
    print('Commands dir: ' + COMMANDS_DIR)
    print('Results dir:  ' + RESULTS_DIR)
    print('Processed:    ' + str(_mcp_commands_processed))
    if _mcp_start_time:
        print('Uptime:       ' + str(int(time.time() - _mcp_start_time)) + 's')
    print('')
    print('Commands:')
    print('  mcp_start()        - Non-blocking background (experimental)')
    print('  mcp_start_timer()  - Alias of mcp_start()')
    print('  mcp_coop_loop()    - Cooperative loop (GUI-friendly)')
    print('  mcp_loop()         - Blocking mode')
    print('  poll_once()        - Process one command')
    print('  mcp_status()       - Show this status')
    print('  mcp_stop()         - Stop polling')
    print('=' * 55)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

ensure_dirs()
write_status('ready', 'Plugin loaded v' + __version__)

print('')
print('=' * 55)
print('Abaqus MCP Plugin v' + __version__ + ' (File IPC)')
print('=' * 55)
print('Home:   ' + MCP_HOME)
print('Abaqus: ' + str(ABAQUS_AVAILABLE))
print('')
print('Start:  mcp_start()     (background, recommended)')
print('        mcp_loop()      (blocking)')
print('Stop:   mcp_stop()')
print('Status: mcp_status()')
print('=' * 55)
_log('INFO', 'Plugin loaded v' + __version__)
