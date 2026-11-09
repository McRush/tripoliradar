"""License notices for the libraries packed into the Windows build (TripoliRadar.spec).

The build carries copies of the Python packages the viewer imports, of the Python runtime and of the PyInstaller
bootloader, so their license texts go with it. After PyInstaller has collected the files, the spec calls
write_notices(): it finds the installed distributions those files come from and writes one text file with the
name, version and license of each, followed by the license texts the distributions carry in their metadata.
Texts a distribution doesn't carry are kept in LICENSES/ and listed in VENDORED. A distribution with no license
text either way stops the build, so a new dependency can't slip into the build without its license."""
import importlib.metadata as metadata
import os
import platform
import sys

# license texts missing from the distribution metadata, kept in LICENSES/ (copied next to the exe)
VENDORED = {
    'pygame-ce': ['LICENSES/pygame-ce/'],           # LGPL-2.1 and the SDL family of DLLs in its wheel
    'pyopengl': ['LICENSES/BSD-PyOpenGL.txt'],
}
# in the build without any collected file of their own, with what of them is there
ALWAYS = {'pyinstaller': 'its bootloader is TripoliRadar.exe'}
# where the metadata names the license poorly
LICENSE_OVERRIDE = {
    'pyinstaller': 'GPL-2.0-or-later with the bootloader exception (programs built with it may use any license)',
}
LICENSE_NAMES = ('LICEN', 'COPYING', 'NOTICE')
RULE = '=' * 100


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _key(name):
    return name.lower().replace('_', '-')


def bundled_distributions(sources):
    """The installed distributions that own any of the files in sources (paths), plus ALWAYS, sorted by name."""
    sources = {_norm(s) for s in sources if isinstance(s, str)}
    found = {}
    for dist in metadata.distributions():
        name = _key(dist.metadata['Name'])
        if name not in found and any(_norm(dist.locate_file(f)) in sources for f in dist.files or ()):
            found[name] = dist
    for name in ALWAYS:
        found.setdefault(name, metadata.distribution(name))
    return [found[k] for k in sorted(found)]


def license_name(dist):
    md = dist.metadata
    if _key(md['Name']) in LICENSE_OVERRIDE:
        return LICENSE_OVERRIDE[_key(md['Name'])]
    if md.get('License-Expression'):
        return md['License-Expression']
    lic = (md.get('License') or '').strip()
    if lic and '\n' not in lic and len(lic) <= 80:
        return lic
    classifiers = [c.split('::')[-1].strip() for c in md.get_all('Classifier') or ()
                   if c.startswith('License ::') and not c.endswith('OSI Approved')]
    return ', '.join(classifiers) or 'see the text'


def license_files(dist):
    """The license files in the distribution's .dist-info: everything under licenses/ (PEP 639) and the files
    named like LICENSE / COPYING / NOTICE."""
    return [f for f in dist.files or ()
            if f.parts[0].endswith('.dist-info')
            and ((len(f.parts) > 2 and f.parts[1] == 'licenses') or any(k in f.name.upper() for k in LICENSE_NAMES))]


def _vendored(name, project_dir):
    paths = VENDORED.get(name, [])
    for rel in paths:
        path = os.path.join(project_dir, rel)
        if not os.path.exists(path) or (os.path.isdir(path) and not os.listdir(path)):
            raise SystemExit(f"thirdparty: {rel} (license of {name}) is missing")
    return paths


def _read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read().strip()


def write_notices(out_path, sources, project_dir):
    """Write the notices for the distributions owning sources (the collected files' paths) to out_path."""
    entries = []                                # (title, license, link, [inline (label, text)], [vendored paths])
    py_license = os.path.join(sys.base_prefix, 'LICENSE.txt')
    if not os.path.exists(py_license):
        raise SystemExit(f"thirdparty: Python's license {py_license} not found")
    entries.append((f'Python {platform.python_version()} (the runtime and the standard library)',
                    'PSF-2.0', 'https://www.python.org/',
                    [('LICENSE.txt', _read(py_license))], ['LICENSES/Python-incorporated-software.txt']))
    for dist in bundled_distributions(sources):
        name = _key(dist.metadata['Name'])
        inline = [(str(f), _read(dist.locate_file(f))) for f in license_files(dist)]
        vendored = _vendored(name, project_dir)
        if not inline and not vendored:
            raise SystemExit(f"thirdparty: no license text for {dist.metadata['Name']}: "
                             "put it in LICENSES/ and add it to VENDORED in tools/thirdparty.py")
        title = f"{dist.metadata['Name']} {dist.version}" + (f' ({ALWAYS[name]})' if name in ALWAYS else '')
        link = f"https://pypi.org/project/{dist.metadata['Name']}/{dist.version}/"
        entries.append((title, license_name(dist), link, inline, vendored))

    out = ['Libraries included in this build of TripoliRadar, and their licenses.',
           'Source code of each library is available at the link given for it.', '']
    for title, lic, link, inline, vendored in entries:
        where = ', '.join(['below'] * bool(inline) + vendored)
        out += [f'  {title}', f'      License: {lic}', f'      Source:  {link}', f'      Text:    {where}', '']
    for title, lic, link, inline, vendored in entries:
        for label, text in inline:
            out += ['', RULE, f'{title}: {label}', RULE, '', text, '']
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(out).rstrip() + '\n')
    return [e[0] for e in entries]
