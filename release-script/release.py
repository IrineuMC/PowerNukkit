#  https://PowerNukkit.org - The Nukkit you know but Powerful!
#  Copyright (C) 2021  José Roberto de Araújo Júnior
# 
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
# 
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
# 
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.

from xml.dom import minidom

from release_utils import *

pom_doc = minidom.parse('pom.xml')
project_name = None
project_version = None
is_windows = os.name == 'nt'

version_tags = pom_doc.getElementsByTagName('version')
project_version_tag = None
for version_tag in version_tags:
    if version_tag.parentNode is not None and version_tag.parentNode.nodeName == 'project':
        project_version_tag = version_tag
        project_version = get_text(version_tag.childNodes)
        break

name_tags = pom_doc.getElementsByTagName('name')
for name_tag in name_tags:
    if name_tag.parentNode is not None and name_tag.parentNode.nodeName == 'project':
        project_name = get_text(name_tag.childNodes)
        break

check(project_version is not None, "Could not parse the project version!")
check(project_name is not None, "Could not parse the project name!")
check(len(project_version) > 0, "The project version cannot be empty!")
check(len(project_name) > 0, "The project name cannot be empty!")
check(project_version.upper() == project_version, "The project version must be all uppercase!")
check("SNAPSHOT" not in project_version, "Attempted to release a snapshot version! Version: " + project_version)
check("CUSTOM" not in project_version, "The release version has CUSTOM identifier! Make sure to remove it if the "
                                       "project state is really ready for publishing! Version: " + project_version)
check('-PN' in project_version, "The project version is missing the '-PN' suffix! Version: " + project_version)

print("-> Preparing to publish", project_name, project_version)

git_is_dirty = cmd('git', 'status', '--porcelain')
if not ignore_dirty_state:
    check(not git_is_dirty, "The workspace is dirty!\n" + git_is_dirty)


git_tag = 'v' + project_version
try:
    existing_tag = cmd('git', 'rev-parse', git_tag)
    raise failure("The tag " + git_tag + " is already referencing the commit " + existing_tag)
except subprocess.CalledProcessError as err:
    print("-> The tag", git_tag, "is available")

mvn = 'mvn'
if use_mvn_wrapper:
    if is_windows:
        mvn = 'mvnw.cmd'
    else:
        mvn = './mvnw'

print("-> Checking java version")
for version_line in subprocess.check_output([mvn, '-version']).decode().strip().split("\n"):
    if "Java version: " in version_line:
        print("->", version_line)
        if check_java_version and "Java version: 1.8" not in version_line:
            raise failure(project_name + " must be built with Java 8 (1.8)")

if run_test_build:
    print('-> Executing a test build with', mvn, 'clean package')
    args = [mvn, 'clean', 'package', '-Dmaven.javadoc.skip=true']
    if not run_tests:
        args += '-Dmaven.test.skip=true'
    status_code = subprocess.call(args)
    check(status_code == 0, "Could not execute a normal build! Maven returned status code " + str(status_code))

if create_git_branch:
    git_branch = 'release/' + git_tag
    if git_branch != cmd('git', 'branch', '--show-current'):
        print("-> Creating branch", git_branch)
        cmd('git', 'checkout', '-b', git_branch)

if create_git_commit:
    print("-> Executing the build commit")
    commit_cmd('git', 'commit', '--allow-empty', '-m', 'Releasing ' + project_name + ' ' + git_tag)

if create_git_tag:
    print("-> Creating tag", git_tag)
    cmd('git', 'tag', git_tag)

docker_tags = []
try:
    if run_docker_build:
        docker_version = project_version.replace('-PN', '')
        docker_tag = docker_tag_prefix + ':' + docker_version
        docker_tags += docker_tag
        print('-> Executing a docker build for tag', docker_tag)
        status_code = subprocess.call(('docker', 'build', '-t', docker_tag, '.'))
        check(status_code == 0, "Could not execute a docker build! Status code: " + str(status_code))

        docker_version_parts = docker_version.split('.')
        for i in range(len(docker_version_parts)):
            docker_version_parts.pop()
            if len(docker_version_parts) > 0 and docker_version_parts[-1].isnumeric():
                docker_sub_version = ".".join(docker_version_parts)
            elif len(docker_version_parts) == 0:
                docker_sub_version = "latest"
            else:
                break

            if docker_sub_version is not None:
                docker_sub_tag = docker_tag_prefix + ':' + docker_sub_version
                print("-> Adding docker tag", docker_sub_tag)
                cmd('docker', 'tag', docker_tag, docker_sub_tag)
                docker_tags += docker_tag

    if run_maven_deploy:
        print('-> Executing a maven deploy with', mvn, 'clean deploy')
        args = [mvn, 'clean', 'deploy']
        if run_test_build or not run_tests:
            args += '-Dmaven.test.skip=true'
        status_code = subprocess.call(args)
        check(status_code == 0, "Could not execute the maven deploy! Maven returned status code " + str(status_code))
except Exception as e:
    print("-> The release has failed! Removing release commit and the git tag", git_tag, file=sys.stderr)
    cmd('git', 'tag', '-d', git_tag)
    cmd('git', 'reset', '--soft', 'HEAD~1')
    raise e

if run_docker_push:
    for docker_tag_to_push in docker_tags:
        print('-> Pushing docker tag', docker_tag_to_push)
        cmd('docker', 'push', docker_tag_to_push)

if update_pom_version:
    next_version = project_version + '-CUSTOM'
    print('-> Changing the project version to', next_version)
    for node in project_version_tag.childNodes:
        project_version_tag.removeChild(node)
    project_version_tag.appendChild(pom_doc.createTextNode(next_version))

    pom_output = open("pom.xml", "w")
    try:
        pom_doc.documentElement.writexml(pom_output)
    finally:
        pom_output.close()

    if create_git_commit:
        cmd('git', 'add', 'pom.xml')
        cmd('git', 'commit', '-m', 'Version changed to ' + next_version)

if run_git_push:
    if create_git_commit:
        print("-> Pushing commits to the Git repository")
        cmd('git', 'push')
    if create_git_tag:
        print("-> Pushing tag", git_tag, "to the Git repository")
        cmd('git', 'push', 'origin', git_tag)

print("-> The build script has completed")