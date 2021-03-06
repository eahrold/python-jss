#!/usr/bin/env python
"""distribution_points.py

Utility classes for synchronizing packages and scripts to Jamf file
repositories.

Copyright (C) 2014 Shea G Craig <shea.craig@da.org>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""


import os
import shutil
import subprocess
import sys
import urllib

import casper
from .contrib import requests


PKG_TYPES = ['.PKG', '.DMG']
PKG_FILE_TYPE = 0
#EBOOK_FILE_TYPE = 1 ???
#IN_HOUSE_APP_FILE_TYPE = 2 ???
#SCRIPT_TYPES = []
SCRIPT_FILE_TYPE = 3

class DistributionPoints(object):
    """DistributionPoints is an object which reads DistributionPoint
    configuration data from the JSS and serves as a container for objects
    representing the configured distribution points.

    This class provides an abstract interface for uploading packages and dmg's
    to file repositories.

    PLEASE NOTE: Not all DistributionPoint types support all of the available
    methods. For example, JDS' do not have a copy_script() method, and there
    are caveats to the reliability of the exists() method.

    Support for AFP/SMB shares and JDS' are included, and are selected based on
    configuration files. Planned support for HTTP(S) and CDP types will come
    later.

    This object can copy files to multiple repositories, avoiding the need to
    use Casper Admin to "Replicate" from one repository to another, as long as
    the repositories are all configured correctly.

    See the individual Repository subclasses for information regarding
    type-specific properties and configuration.

    """
    def __init__(self, jss):
        """Populate our distribution point dict from our configuration file.

        The JSS server's DistributionPoints is used to automatically configure
        AFP and SMB shares. To make use of this, the repo's dictionary should
        contain only the name of the repo, as found in the web interface, and
        the password for the RW user. This method is deprecated, and you should
        fully specify the required connection arguments for each DP in the
        future.

        Please see the docstrings for the different DistributionPoint
        subclasses for information regarding required configuration information
        and properties.

        jss:      JSS server object

        """
        self.jss = jss
        self._children = []
        self.dp_info = self.jss.DistributionPoint().retrieve_all()

        # If no distribution points are configured, there's nothing to do here.
        if self.jss.repo_prefs:
            # Set up a counter for avoiding name clashes with optional name
            # variable.
            counter = 0
            for repo in self.jss.repo_prefs:
                # Handle AFP/SMB shares, as they can be auto-configured.
                # Legacy system did not require explicit type key.
                if not repo.get('type'):
                    # Must be AFP or SMB.
                    # Use JSS.DistributionPoints information to automatically
                    # configure this DP.
                    for dp_object in self.dp_info:
                        if repo['name'] == dp_object.findtext('name'):
                            name = dp_object.findtext('name')
                            URL = dp_object.findtext('ip_address')
                            connection_type = \
                                dp_object.findtext('connection_type')
                            share_name = dp_object.findtext('share_name')
                            domain = dp_object.findtext('workgroup_or_domain')
                            port = dp_object.findtext('share_port')
                            username = \
                                dp_object.findtext('read_write_username')
                            password = repo.get('password')

                            mount_point = os.path.join('/Volumes',
                                    (name + share_name).replace(' ', ''))

                            if connection_type == 'AFP':
                                dp = AFPDistributionPoint(URL=URL, port=port,
                                    share_name=share_name,
                                    mount_point=mount_point,
                                    username=username, password=password)
                            elif connection_type == 'SMB':
                                dp = SMBDistributionPoint(URL=URL, port=port,
                                    share_name=share_name,
                                    mount_point=mount_point,
                                    domain=domain, username=username,
                                    password=password)

                            # No need to keep looping.
                            break

                # Handle Explictly declared DP's.
                elif repo.get('type') in ['AFP', 'SMB']:
                    name = repo.get('name') or 'JSS_DP_%02i' % counter
                    counter += 1
                    URL = repo['URL']
                    # If found, strip the scheme off the URL
                    # it's reconstructed later
                    if "://" in URL:
                        URL = URL.split('://')[1]

                    connection_type = repo['type']
                    share_name = repo['share_name']
                    # Domain is not used for AFP.
                    domain = repo.get('domain')
                    username = repo['username']
                    password = repo['password']

                    mount_point = os.path.join('/Volumes',
                                               name.replace(' ', ''))

                    if connection_type == 'AFP':
                        # If port isn't given, assume it's the std of 548.
                        port = repo.get('port') or '548'
                        dp = AFPDistributionPoint(URL=URL, port=port,
                                                share_name=share_name,
                                                mount_point=mount_point,
                                                username=username,
                                                password=password)
                    elif connection_type == 'SMB':
                        # If port isn't given, assume it's the std of 139.
                        port = repo.get('port') or '139'
                        dp = SMBDistributionPoint(URL=URL, port=port,
                                                share_name=share_name,
                                                mount_point=mount_point,
                                                domain=domain,
                                                username=username,
                                                password=password)

                elif repo.get('type') == 'JDS':
                    dp = JDS(jss=self.jss, URL=repo['URL'],
                             username=repo['username'],
                             password=repo['password'])
                else:
                    raise ValueError('Distribution Point Type not recognized.')

                # Add the DP to the list.
                self._children.append(dp)

    def add_distribution_point(self, dp):
        """Add a distribution point to the list."""
        self._children.append(dp)

    def remove_distribution_point(self, index):
        """Remove a distribution point by index."""
        self._children.pop(index)

    def copy(self, filename, id_=-1):
        """Copy file to all repos, guessing file type and destination based
        on its extension.

        filename:       String path to the local file to copy.
        id_:            Package or Script object ID to target. For use with
                        JDS DP's only.

        """
        extension = os.path.splitext(filename)[1].upper()
        for repo in self._children:
            if extension in PKG_TYPES:
                repo.copy_pkg(filename, id_)
            else:
                # All other file types can go to scripts.
                repo.copy_script(filename, id_)

    def copy_pkg(self, filename):
        """Copy a pkg or dmg to all repositories.

        filename:       String path to the local file to copy.

        """
        for repo in self._children:
            repo.copy_pkg(filename, id_)

    def copy_script(self, filename):
        """Copy a script to all repositories.

        filename:       String path to the local file to copy.

        """
        for repo in self._children:
            repo.copy_script(filename, id_)

    def mount(self):
        """Mount all mountable distribution points."""
        for child in self._children:
            if hasattr(child, 'mount'):
                child.mount()

    def umount(self):
        """Umount all mountable distribution points."""
        for child in self._children:
            if hasattr(child, 'umount'):
                child.umount()

    def exists(self, filename):
        """Report whether a file exists on all distribution points. Determines
        file type by extension.

        filename:       Filename you wish to check. (No path! e.g.:
                        "AdobeFlashPlayer-14.0.0.176.pkg")

        """
        result = True
        extension = os.path.splitext(filename)[1].upper()
        for repo in self._children:
            if not repo.exists(filename):
                result = False

        return result

    def __repr__(self):
        """Nice display of our file shares."""
        output = ''
        index = 0
        for child in self._children:
            output += 79 * '-' + '\n'
            output += 'Index: %s' % index
            output += child.__repr__()
            index += 1

        return output


class Repository(object):
    """Base class for file repositories."""
    def __init__(self, **connection_args):
        """Store the connection information."""
        if self.required_attrs.issubset(set(connection_args.keys())):
            self.connection = {}
            for key, value in connection_args.iteritems():
                self.connection[key] = value

            self._build_url()
        else:
            # Put a custom exception in here.
            missing_attrs = self.required_attrs.difference(set(connection_args.keys()))
            raise Exception("Missing REQUIRED argument(s) %s to %s"
                            "distribution point." % (list(missing_attrs),
                                                     self.__class__))

    # Not really needed, since all subclasses implement this.
    # Placeholder for whether I do want to formally specify the interface
    # like this.
    #def _copy(self, filename):
    #    raise NotImplementedError("Base class 'Repository' should not be used "
    #                              "for copying!")

    def __repr__(self):
        output = ''
        output += "\nDistribution Point: %s\n" % self.connection['URL']
        output += "Type: %s\n" % type(self)
        output += "Connection Information:\n"
        for key, val in self.connection.items():
            output += "\t%s: %s\n" % (key, val)

        return output


class MountedRepository(Repository):
    def __init__(self, **connection_args):
        super(MountedRepository, self).__init__(**connection_args)

    def _build_url(self):
        pass

    def mount(self, nobrowse=False):
        """Mount the repository.

        If you want it to be hidden from the GUI, pass nobrowse=True.

        """
        # Is this volume already mounted; if so, we're done.
        if not self.is_mounted():

            # First, ensure the mountpoint exists
            if not os.path.exists(self.connection['mount_point']):
                os.mkdir(self.connection['mount_point'])

            # Try to mount
            args = ['mount', '-t', self.protocol, self.connection['mount_url'],
                    self.connection['mount_point']]
            if nobrowse:
                args.insert(1, '-o')
                args.insert(2, 'nobrowse')

            subprocess.check_call(args)

    def umount(self):
        """Try to unmount our mount point."""
        # If not mounted, don't bother.
        if os.path.exists(self.connection['mount_point']):
            # Force an unmount. If you are manually mounting and unmounting
            # shares with python, chances are good that you know what you are
            # doing and *want* it to unmount. For real.
            if sys.platform == 'darwin':
                subprocess.check_call(['/usr/sbin/diskutil', 'unmount',
                                       'force',
                                       self.connection['mount_point']])
            else:
                subprocess.check_call(['umount', '-f',
                                       self.connection['mount_point']])

    def is_mounted(self):
        """Test for whether a mount point is mounted."""
        return os.path.ismount(self.connection['mount_point'])

    def copy_pkg(self, filename, id_=-1):
        """Copy a package to the repo's subdirectory.

        filename:           Path for file to copy.
        id_:                Ignored. Used for compatibility with JDS repos.

        """
        basename = os.path.basename(filename)
        self._copy(filename, os.path.join(self.connection['mount_point'],
                                          'Packages', basename))

    def copy_script(self, filename, id_=-1):
        """Copy a script to the repo's Script subdirectory.

        filename:           Path for file to copy.
        id_:                Ignored. Used for compatibility with JDS repos.

        """
        basename = os.path.basename(filename)
        self._copy(filename, os.path.join(self.connection['mount_point'],
                                          'Scripts', basename))

    def _copy(self, filename, destination):
        """Copy a file to the repository. Handles folders and single files.
        Will mount if needed.

        """
        if not self.is_mounted():
            self.mount()

        full_filename = os.path.abspath(os.path.expanduser(filename))

        if os.path.isdir(full_filename):
            shutil.copytree(full_filename, destination)
        elif os.path.isfile(full_filename):
            shutil.copyfile(full_filename, destination)

    def exists(self, filename):
        """Report whether a file exists on the distribution point. Determines
        file type by extension.

        filename:       Filename you wish to check. (No path! e.g.:
                        "AdobeFlashPlayer-14.0.0.176.pkg")

        """
        extension = os.path.splitext(filename)[1].upper()
        if extension in PKG_TYPES:
            filepath = os.path.join(self.connection['mount_point'],
                                    'Packages', filename)
        else:
            filepath = os.path.join(self.connection['mount_point'],
                                    'Scripts', filename)
        return os.path.exists(filepath)

    def __repr__(self):
        """Add mount status to output."""
        output = super(MountedRepository, self).__repr__()
        output += "Mounted: %s\n" % \
            os.path.ismount(self.connection['mount_point'])
        return output

    def _encode_password(self):
        """Returns a safely encoded and quoted password."""
        upass = unicode(self.connection['password']).encode('utf-8')
        return urllib.quote(upass, safe='~()*!.\'')

class AFPDistributionPoint(MountedRepository):
    """Represents an AFP repository.

    Please note: OS X seems to cache credentials when you use mount_afp like
    this, so if you change your authentication information, you'll have to
    force a re-authentication.

    """
    protocol = 'afp'
    required_attrs = {'URL', 'mount_point', 'username', 'password'}

    def __init__(self, **connection_args):
        """Set up an AFP connection.
        Required connection arguments:
        URL:            URL to the mountpoint in the format, including volume
                        name Ex:
                        'my_repository.domain.org/jamf'
                        (Do _not_ include protocol or auth info.)
        mount_point:    Path to a valid mount point.
        username:       For shares requiring authentication, the username.
        password:       For shares requiring authentication, the password.

        """
        super(AFPDistributionPoint, self).__init__(**connection_args)

    def _build_url(self):
        """Helper method for building mount URL strings."""
        if self.connection.get('username') and self.connection.get('password'):
            auth = "%s:%s@" % (self.connection['username'],
                               self._encode_password())
        else:
            auth = ''

        # Optional port number
        if self.connection.get('port'):
            port = ":%s" % self.connection['port']
        else:
            port = ''

        self.connection['mount_url'] = '%s://%s%s%s/%s' % (
            self.protocol, auth, self.connection['URL'], port,
            self.connection['share_name'])


class SMBDistributionPoint(MountedRepository):
    protocol = 'smbfs'
    required_attrs = {'URL', 'mount_point', 'domain', 'username', 'password'}

    def __init__(self, **connection_args):
        """Set up a SMB connection.
        Required connection arguments:
        URL:            URL to the mountpoint in the format, including volume
                        name Ex:
                        'my_repository.domain.org/jamf'
                        (Do _not_ include protocol or auth info.)
        mount_point:    Path to a valid mount point.
        domain:    Specify the domain.
        username:       For shares requiring authentication, the username.
        password:       For shares requiring authentication, the password.

        """
        super(SMBDistributionPoint, self).__init__(**connection_args)

    def _build_url(self):
        """Helper method for building mount URL strings."""
        # Build auth string
        if self.connection.get('username') and self.connection.get('password'):
            auth = "%s:%s@" % (self.connection['username'],
                               self._encode_password())
            if self.connection.get('domain'):
                auth = r"%s;%s" % (self.connection['domain'], auth)
        else:
            auth = ''

        # Optional port number
        if self.connection.get('port'):
            port = ":%s" % self.connection['port']
        else:
            port = ''

        # Construct mount_url
        self.connection['mount_url'] = '//%s%s%s/%s' % (
            auth, self.connection['URL'], port, self.connection['share_name'])


class JDS(Repository):
    """Class for representing a JDS and associated repositories.

    The JDS has a folder to which packages are uploaded. From there, the JDS
    handles the distribution to its managed distribution points.

    This distribution point type cannot copy scripts.

    Also, there are caveats to its .exists() method which you should be aware
    of before relying on it.

    I'm not sure, but I imagine that organizations with a JDS will not also
    have other types of DP's, so it may be sufficient to just use the JDS class
    directly rather than as member of a DistributionPoints object.

    """
    required_attrs = {'jss', 'URL', 'username', 'password'}

    def __init__(self, **connection_args):
        """Set up a connection to a JDS.
        Required connection arguments:
            jss:            A JSS Object.
            URL:            URL to the JSS to upload to.
            username:       The read/write account name.
            password:       Password for above.

        """
        super(JDS, self).__init__(**connection_args)

    def _build_url(self):
        """Builds the URL to POST files to."""
        self.connection['upload_url'] = '%s/%s' % \
                (self.connection.get('URL'), 'dbfileupload')

    def copy_pkg(self, filename, id_='-1'):
        """Copy a package to the JDS.

        Required Parameters:
        filename:           Full path to file to upload.
        id_:                ID of Package object to associate with, or '-1' for
                            new packages (default).

        """
        self._copy(filename, id_=id_, file_type=PKG_FILE_TYPE)

    def copy_script(self, filename, id_='-1'):
        """Copy a script to the JDS. At this point, it does not work correctly.

        Scripts end up in the JDS including the multipart boundaries and some
        HTML information, which is not correct. So don't use this yet!

        Required Parameters:
        filename:           Full path to file to upload.
        id_:                ID of Package object to associate with, or '-1' for
                            new packages (default).

        """
        self._copy(filename, id_=id_, file_type=SCRIPT_FILE_TYPE)

    def _copy(self, filename, id_='-1', file_type=0):
        """Upload a file to the JDS."""
        basefname = os.path.basename(filename)
        extension = os.path.splitext(basefname)[1].upper()

        resource = {basefname: open(filename, 'rb')}
        headers = {'Content-Type': 'text/xml', 'DESTINATION': '1',
                   'OBJECT_ID': str(id_), 'FILE_TYPE': file_type,
                   'FILE_NAME': basefname}
        response = requests.post(url=self.connection['upload_url'],
                                 files=resource,
                                 auth=(self.connection['username'],
                                       self.connection['password']),
                                 headers=headers)
        print(response, response.text)
        return response

    def exists(self, filename):
        """Check for the existence of a file on the JDS.

        Unlike other DistributionPoint types, JDS' have no documented interface
        for checking whether the JDS and its children have a complete copy of a
        file. The best we can do is check for a package object using the API
        /packages URL--JSS.Pacakage() and look for matches on the filename.

        If this is not enough, please use the alternate exists methods. For
        example, it's possible to create a Package object but never upload a
        package file, and this method will still return "True".

        Also, this may be slow, as it needs to retrieve the complete list of
        packages from the server.

        """
        # Technically, the results of the casper.jxml page list the package
        # files on the server. This is an undocumented interface, however.
        result = False
        packages = self.connection['jss'].Package().retrieve_all()
        for package in packages:
            if package.findtext('filename') == filename:
                result = True
                break

        return result

    def exists_using_casper(self, filename):
        """Check for the existence of a file on the JDS.

        Unlike other DistributionPoint types, JDS' have no documented interface
        for checking whether the JDS and its children have a complete copy of a
        file. The best we can do is check for a package object using the API
        /packages URL--JSS.Pacakage() and look for matches on the filename.

        If this is not enough, this method uses the results of the casper.jxml
        page to determine if a file exists. This is an undocumented feature and
        as such should probably not be relied upon.

        It will test for whether the file exists on ALL configured distribution
        servers. This may register False if the JDS is busy syncing them.
        (Need to test this situation).

        Also, casper.jxml includes checksums. If this method proves reliable,
        checksum comparison will be added as a feature.

        """
        casper_results = casper.Casper(self.connection['jss'])
        distribution_servers = casper_results.find('distributionservers')

        # Step one: Build a list of sets of all package names.
        all_packages = []
        for distribution_server in distribution_servers:
            packages = set()
            for package in distribution_server.findall('packages/package'):
                packages.add(os.path.basename(package.find('fileURL').text))

            all_packages.append(packages)

        # Step two: Intersect the sets.
        base_set = all_packages.pop()
        for packages in all_packages:
            base_set = base_set.intersection(packages)

        # Step three: Check for membership.
        result = filename in base_set

        return result

class HTTPRepository(Repository):
    pass


class HTTPSRepository(Repository):
    pass