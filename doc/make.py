#!/usr/bin/env python

"""
Python script for building documentation.

To build the docs you must have all optional dependencies for pandas
installed. See the installation instructions for a list of these.

<del>Note: currently latex builds do not work because of table formats that are not
supported in the latex generation.</del>

2014-01-30: Latex has some issues but 'latex_forced' works ok for 0.13.0-400 or so

Usage
-----
python make.py clean
python make.py html
"""
from __future__ import print_function

import glob
import os
import shutil
import sys
import sphinx
import argparse
import jinja2

os.environ['PYTHONPATH'] = '..'

SPHINX_BUILD = 'sphinxbuild'


def upload_dev(user='pandas'):
    'push a copy to the pydata dev directory'
    if os.system('cd build/html; rsync -avz . {0}@pandas.pydata.org'
                 ':/usr/share/nginx/pandas/pandas-docs/dev/ -essh'.format(user)):
        raise SystemExit('Upload to Pydata Dev failed')


def upload_dev_pdf(user='pandas'):
    'push a copy to the pydata dev directory'
    if os.system('cd build/latex; scp pandas.pdf {0}@pandas.pydata.org'
                 ':/usr/share/nginx/pandas/pandas-docs/dev/'.format(user)):
        raise SystemExit('PDF upload to Pydata Dev failed')


def upload_stable(user='pandas'):
    'push a copy to the pydata stable directory'
    if os.system('cd build/html; rsync -avz . {0}@pandas.pydata.org'
                 ':/usr/share/nginx/pandas/pandas-docs/stable/ -essh'.format(user)):
        raise SystemExit('Upload to stable failed')


def upload_stable_pdf(user='pandas'):
    'push a copy to the pydata dev directory'
    if os.system('cd build/latex; scp pandas.pdf {0}@pandas.pydata.org'
                 ':/usr/share/nginx/pandas/pandas-docs/stable/'.format(user)):
        raise SystemExit('PDF upload to stable failed')


def upload_prev(ver, doc_root='./', user='pandas'):
    'push a copy of older release to appropriate version directory'
    local_dir = f'{doc_root}build/html'
    remote_dir = f'/usr/share/nginx/pandas/pandas-docs/version/{ver}/'
    cmd = 'cd %s; rsync -avz . %s@pandas.pydata.org:%s -essh'
    cmd %= (local_dir, user, remote_dir)
    print(cmd)
    if os.system(cmd):
        raise SystemExit(f'Upload to {remote_dir} from {local_dir} failed')

    local_dir = f'{doc_root}build/latex'
    pdf_cmd = 'cd %s; scp pandas.pdf %s@pandas.pydata.org:%s'
    pdf_cmd %= (local_dir, user, remote_dir)
    if os.system(pdf_cmd):
        raise SystemExit(f'Upload PDF to {ver} from {doc_root} failed')

def build_pandas():
    os.chdir('..')
    os.system('python setup.py clean')
    os.system('python setup.py build_ext --inplace')
    os.chdir('doc')

def build_prev(ver):
    if os.system(f'git checkout v{ver}') != 1:
        os.chdir('..')
        os.system('python setup.py clean')
        os.system('python setup.py build_ext --inplace')
        os.chdir('doc')
        os.system('python make.py clean')
        os.system('python make.py html')
        os.system('python make.py latex')
        os.system('git checkout master')


def clean():
    if os.path.exists('build'):
        shutil.rmtree('build')

    if os.path.exists('source/generated'):
        shutil.rmtree('source/generated')


def html():
    check_build()
    if os.system('sphinx-build -P -b html -d build/doctrees '
                 'source build/html'):
        raise SystemExit("Building HTML failed.")
    try:
        # remove stale file
        os.system('cd build; rm -f html/pandas.zip;')
    except:
        pass

def zip_html():
    try:
        print("\nZipping up HTML docs...")
        # just in case the wonky build box doesn't have zip
        # don't fail this.
        os.system('cd build; rm -f html/pandas.zip; zip html/pandas.zip -r -q html/* ')
        print("\n")
    except:
        pass

def latex():
    check_build()
    if sys.platform != 'win32':
        # LaTeX format.
        if os.system('sphinx-build -b latex -d build/doctrees '
                     'source build/latex'):
            raise SystemExit("Building LaTeX failed.")
        # Produce pdf.

        os.chdir('build/latex')

        # Call the makefile produced by sphinx...
        if os.system('make'):
            print("Rendering LaTeX failed.")
            print("You may still be able to get a usable PDF file by going into 'build/latex'")
            print("and executing 'pdflatex pandas.tex' for the requisite number of passes.")
            print("Or using the 'latex_forced' target")
            raise SystemExit

        os.chdir('../..')
    else:
        print('latex build has not been tested on windows')

def latex_forced():
    check_build()
    if sys.platform != 'win32':
        # LaTeX format.
        if os.system('sphinx-build -b latex -d build/doctrees '
                     'source build/latex'):
            raise SystemExit("Building LaTeX failed.")
        # Produce pdf.

        os.chdir('build/latex')

        # Manually call pdflatex, 3 passes should ensure latex fixes up
        # all the required cross-references and such.
        os.system('pdflatex -interaction=nonstopmode pandas.tex')
        os.system('pdflatex -interaction=nonstopmode pandas.tex')
        os.system('pdflatex -interaction=nonstopmode pandas.tex')
        raise SystemExit("You should check the file 'build/latex/pandas.pdf' for problems.")

    else:
        print('latex build has not been tested on windows')


def check_build():
    build_dirs = [
        'build', 'build/doctrees', 'build/html',
        'build/latex', 'build/plots', 'build/_static',
        'build/_templates']
    for d in build_dirs:
        try:
            os.mkdir(d)
        except OSError:
            pass


def all():
    # clean()
    html()


def auto_dev_build(debug=False):
    msg = ''
    try:
        step = 'clean'
        clean()
        step = 'html'
        html()
        step = 'upload dev'
        upload_dev()
        if not debug:
            sendmail(step)

        step = 'latex'
        latex()
        step = 'upload pdf'
        upload_dev_pdf()
        if not debug:
            sendmail(step)
    except (Exception, SystemExit) as inst:
        msg = str(inst) + '\n'
        sendmail(step, f'[ERROR] {msg}')


def sendmail(step=None, err_msg=None):
    from_name, to_name = _get_config()

    if step is None:
        step = ''

    if err_msg is None or '[ERROR]' not in err_msg:
        msgstr = f'Daily docs {step} completed successfully'
        subject = f"DOC: {step} successful"
    else:
        msgstr = err_msg
        subject = f"DOC: {step} failed"

    import smtplib
    from email.MIMEText import MIMEText
    msg = MIMEText(msgstr)
    msg['Subject'] = subject
    msg['From'] = from_name
    msg['To'] = to_name

    server_str, port, login, pwd = _get_credentials()
    server = smtplib.SMTP(server_str, port)
    server.ehlo()
    server.starttls()
    server.ehlo()

    server.login(login, pwd)
    try:
        server.sendmail(from_name, to_name, msg.as_string())
    finally:
        server.close()


def _get_dir(subdir=None):
    import getpass
    USERNAME = getpass.getuser()
    if sys.platform == 'darwin':
        HOME = f'/Users/{USERNAME}'
    else:
        HOME = f'/home/{USERNAME}'

    if subdir is None:
        subdir = '/code/scripts/config'
    return f'{HOME}/{subdir}'


def _get_credentials():
    tmp_dir = _get_dir()
    cred = f'{tmp_dir}/credentials'
    with open(cred, 'r') as fh:
        server, port, un, domain = fh.read().split(',')
    port = int(port)
    login = f'{un}@{domain}.com'

    import base64
    with open(f'{tmp_dir}/cron_email_pwd', 'r') as fh:
        pwd = base64.b64decode(fh.read())

    return server, port, login, pwd


def _get_config():
    tmp_dir = _get_dir()
    with open(f'{tmp_dir}/addresses', 'r') as fh:
        from_name, to_name = fh.read().split(',')
    return from_name, to_name

funcd = {
    'html': html,
    'zip_html': zip_html,
    'upload_dev': upload_dev,
    'upload_stable': upload_stable,
    'upload_dev_pdf': upload_dev_pdf,
    'upload_stable_pdf': upload_stable_pdf,
    'latex': latex,
    'latex_forced': latex_forced,
    'clean': clean,
    'auto_dev': auto_dev_build,
    'auto_debug': lambda: auto_dev_build(True),
    'build_pandas': build_pandas,
    'all': all,
}

small_docs = False

# current_dir = os.getcwd()
# os.chdir(os.path.dirname(os.path.join(current_dir, __file__)))

import argparse
argparser = argparse.ArgumentParser(description="""
pandas documentation builder
""".strip())

# argparser.add_argument('-arg_name', '--arg_name',
#                    metavar='label for arg help',
#                    type=str|etc,
#                    nargs='N|*|?|+|argparse.REMAINDER',
#                    required=False,
#                    #choices='abc',
#                    help='help string',
#                    action='store|store_true')

# args = argparser.parse_args()

#print args.accumulate(args.integers)

def generate_index(api=True, single=False, **kwds):
    from jinja2 import Template
    with open("source/index.rst.template") as f:
        t = Template(f.read())

    with open("source/index.rst","w") as f:
        f.write(t.render(api=api,single=single,**kwds))

import argparse
argparser = argparse.ArgumentParser(
    description="pandas documentation builder",
    epilog=f"Targets : {funcd.keys()}",
)

argparser.add_argument('--no-api',
                   default=False,
                   help='Ommit api and autosummary',
                   action='store_true')
argparser.add_argument('--single',
                   metavar='FILENAME',
                   type=str,
                   default=False,
                   help='filename of section to compile, e.g. "indexing"')
argparser.add_argument('--user',
                   type=str,
                   default=False,
                   help='Username to connect to the pydata server')

def main():
    args, unknown = argparser.parse_known_args()
    sys.argv = [sys.argv[0]] + unknown
    if args.single:
        args.single = os.path.basename(args.single).split(".rst")[0]

    if 'clean' in unknown:
        args.single=False

    generate_index(api=not args.no_api and not args.single, single=args.single)

    if len(sys.argv) > 2:
        ftype = sys.argv[1]
        ver = sys.argv[2]

        if ftype == 'build_previous':
            build_prev(ver, user=args.user)
        elif ftype == 'upload_previous':
            upload_prev(ver, user=args.user)
    elif len(sys.argv) == 2:
        for arg in sys.argv[1:]:
            func = funcd.get(arg)
            if func is None:
                raise SystemExit(
                    f'Do not know how to handle {arg}; valid args are {list(funcd.keys())}'
                )
            if args.user:
                func(user=args.user)
            else:
                func()
    else:
        small_docs = False
        all()
# os.chdir(current_dir)

if __name__ == '__main__':
    import sys
    sys.exit(main())
