#import git
import httplib2
import os
import re
import sys
import stat
import threading
from inspect import getmembers
from pprint import pprint

from pathlib import Path
from subprocess import call, check_output
from shutil import copyfile

from apiclient import discovery
from apiclient import errors
from apiclient import http
from oauth2client import client
from oauth2client import tools
from oauth2client.file import Storage

try:
    import argparse
    parser = argparse.ArgumentParser(parents=[tools.argparser])
    parser.add_argument('root_file_id')
    parser.add_argument('output_directory')
    flags = parser.parse_args()
except ImportError:
    flags = None

# If modifying these scopes, delete your previously saved credentials
# at ~/.credentials/drive-python-quickstart.json
SCOPES = 'https://www.googleapis.com/auth/drive'
CLIENT_SECRET_FILE = 'client_secrets.json'
APPLICATION_NAME = 'Drive API Python Quickstart'
DRIVE_FOLDER_MIMETYPE = 'application/vnd.google-apps.folder'

#file_queue = Queue()
drive_service = None
vc_mode = 'svn'

completed_paths_file = open('completed_files.txt', 'a')

with open('completed_files.txt', 'r') as f:
    completed_paths = f.readlines()

completed_paths = [x.strip() for x in completed_paths] 

def get_credentials():
    """Gets valid user credentials from storage.

    If nothing has been stored, or if the stored credentials are invalid,
    the OAuth2 flow is completed to obtain the new credentials.

    Returns:
        Credentials, the obtained credential.
    """
    home_dir = os.path.expanduser('~')
    credential_dir = os.path.join(home_dir, '.credentials')
    if not os.path.exists(credential_dir):
        os.makedirs(credential_dir)
    credential_path = os.path.join(credential_dir, 'drivegitter.json')
    store = Storage(credential_path)
    credentials = store.get()
    if not credentials or credentials.invalid:
        flow = client.flow_from_clientsecrets(CLIENT_SECRET_FILE, SCOPES)
        flow.user_agent = APPLICATION_NAME
        if flags:
            credentials = tools.run_flow(flow, store, flags)
        else:  # Needed only for compatibility with Python 2.6
            credentials = tools.run(flow, store)
        print('Storing credentials to ' + credential_path)
    return credentials


def main():
    credentials = get_credentials()
    http = credentials.authorize(httplib2.Http())
    global drive_service
    drive_service = discovery.build('drive', 'v2', http=http)

    output_dir = Path(flags.output_directory)
    output_dir.mkdir(exist_ok=True)
    root_folder = drive_service.files().get(fileId=flags.root_file_id).execute()

    foldername = root_folder['title'].strip()
    
    if vc_mode == 'git':
        output_dir = Path(output_dir, foldername)
        output_dir.mkdir(exist_ok=True)
        os.chdir(output_dir.as_posix())
        repo = git.Repo.init()
    elif vc_mode == 'svn':
        repo_dir = Path(output_dir, '{0}_repo'.format(foldername))
        checkout_dir = Path(output_dir, '{0}_checkout'.format(foldername))
        call(['svnadmin', 'create', repo_dir.as_posix()])
        call(['svn', 'checkout',
              'file:///{0}'.format(repo_dir.as_posix()), checkout_dir.as_posix()])
        
        # Stub the revprop change hook so we  can change the date of commits
        
        #open(hook_filepath, 'a').close()
        hookpath = Path(repo_dir, 'hooks')
        hook_filepath = Path(hookpath, 'pre-revprop-change.bat' if os.name == 'nt' else 'pre-revprop-change')
        #copyfile(Path(hookpath, 'pre-revprop-change.tmpl').as_posix(), hook_filepath.as_posix())
        f = open(hook_filepath.as_posix(), 'w')
        f.write('#!/bin/sh\n')
        f.write('exit 0\n')
        f.close()        
        if os.name != 'nt':
            call(['chmod', '0777', hook_filepath.as_posix()])
        
        output_dir = checkout_dir
        os.chdir(output_dir.as_posix())

    process_folder(flags.root_file_id, output_dir)
    completed_paths_file.close()


def process_folder(folder_id, folder_path):

    root_file = drive_service.files().get(fileId=folder_id).execute()
    children = drive_service.children().list(folderId=folder_id).execute()

    for child in children['items']:
        print("File {0}".format(child['id']))
        process_file(child['id'], folder_path)
        
    completed_paths_file.write(folder_path.as_posix() + "\n")
    completed_paths_file.flush()


def process_file(file_id, parent_path):

    file = drive_service.files().get(fileId=file_id).execute()

    # Google drive allows filenames that end with a space, which must be trimmed
    filename = file['title'].strip()
    file_path = Path(parent_path, filename)
    print(filename.encode('utf-8'))
    file_owner = file['owners'][0]

    if file['mimeType'] == 'application/vnd.google-apps.folder':
    
        if file_path.as_posix() in completed_paths:
            print("Skipping directory: {0}".format(file_path.as_posix()))
            return
    
        if not file_path.exists():
            file_path.mkdir()
            result = vc_add_folder(file_path, 'Added {0}'.format(filename), file['modifiedDate'], file['lastModifyingUser'], file_owner)
            if result != 0:
                sys.exit('An error occurred when adding the folder ' + file_path.as_posix())
        
        process_folder(file_id, file_path)
    elif not os.path.isfile(file_path.as_posix()):


        if not 'downloadUrl' in file:
            output_mt = None
            if file['mimeType'] == 'application/vnd.google-apps.document':
                output_mt = 'application/vnd.oasis.opendocument.text'
            elif file['mimeType'] == 'application/vnd.google-apps.spreadsheet':
                output_mt = 'application/vnd.oasis.opendocument.spreadsheet'
            else:
                print(file['exportLinks'])
                sys.exit('Unknown mimeType ' + file['mimeType'])

            f = open(file_path.as_posix(), 'wb')
            file_content = drive_service.files().export_media(
                fileId=file_id, mimeType=output_mt).execute()
            f.write(file_content)
            f.close()

            result = vc_add_file(file_path)
            if result != 0:
                sys.exit('An error occurred during the add')

            user = file['lastModifyingUser']
            message = 'Added {0}'.format(filename)
            result = vc_commit_file(file_path, message, file['modifiedDate'], user, file_owner)
            if result != 0:
                sys.exit('An error occurred during the commit')
        else:
            process_file_revisions(file, parent_path, file_owner)

        if 'trashed' in file['labels'] and file['labels']['trashed'] == True:
            result = vc_remove_file(file_path, file, file_owner)
            if result != 0:
                sys.exit('An error occurred when removing the file')

            message = 'Removed {0}'.format(file_path.name)
            result = vc_commit_file(
                file_path, message, file['modifiedDate'], file['lastModifyingUser'], file_owner)

            if result != 0:
                sys.exit('An error occurred then committing the changes')


def process_file_revisions(drive_file, parent_path, file_owner):

    filename = drive_file['title'].strip()
    file_path = Path(parent_path, filename)

    revisions = drive_service.revisions().list(
        fileId=drive_file['id']).execute()

    revision_number = 0
    for revision in revisions['items']:
        revision_number += 1
        f = open(file_path.as_posix(), 'wb')
        downloadUri = None
        if 'downloadUrl' in revision:
            downloadUri = revision['downloadUrl']
        elif 'exportLinks' in revision:
            sys.exit('File has no export link')

        response = drive_service._http.request(uri=downloadUri)
        file_content = response[1]
        f.write(file_content)
        f.close()

        result = vc_add_file(file_path)
        if result != 0:
            sys.exit('An error occurred during the file add')

        message = 'Added file {0}'.format(filename) if revision_number == 1 else 'Modified {0} (revision {1})'.format(filename, revision_number)
        result = vc_commit_file(file_path, message, revision['modifiedDate'], revision['lastModifyingUser'], file_owner)

        if result != 0:
            sys.exit('An error occurred during the commit')


def vc_remove_file(file_path, file, file_owner):

    if vc_mode == 'git':
        result = call(['git', 'rm', file_path.as_posix()])
        return result
    elif vc_mode == 'svn':
        result = call(['svn', 'delete', file_path.as_posix()])
        return result


def vc_add_file(file_path):
    if vc_mode == 'git':
        result = call(['git', 'add', '-f', file_path.as_posix()])
        return result

    elif vc_mode == 'svn':
        result = call(['svn', 'add', '--force', file_path.as_posix()])
        return 0


def vc_commit_file(file_path, message, date, modified_by_user, file_owner):

    email = modified_by_user['emailAddress'] if 'emailAddress' in modified_by_user else file_owner['emailAddress']
    
    if vc_mode == 'git':
    
        return call(['git', 'commit',
                     '--message', message,
                     '--author="{0}" <{1}>'.format(
                         modified_by_user['displayName'], email),
                     '--date=' + date,
                     '--allow-empty',
                    file_path.as_posix()])
    
    elif vc_mode == 'svn':
        
        username = modified_by_user['displayName']
        print(username)
        username = (username[0] + re.split('[. ]', username)[-1]).lower()
        print(username)
        result = call(['svn', 'commit',
                     '--message', message,
                     '--username', username])
                     
        if result != 0:
            return result
            
        revision = check_output(['svnversion']).decode('utf-8').rstrip().split(':')[-1]
        result = call(['svn', 'propset', 'svn:date', '--revprop', '-r', revision, date])
        return result
                     
def vc_add_folder(file_path, message, date, modified_by_user, file_owner):
    if vc_mode == 'svn':
        vc_add_file(file_path)
        return vc_commit_file(file_path, message, date, modified_by_user, file_owner)
    
    # git doesn't need to add folders, so return a success code
    return 0
        

def download_file(file_id, local_fd):
    """Download a Drive file's content to the local filesystem.

    Args:
      service: Drive API Service instance.
      file_id: ID of the Drive file that will downloaded.
      local_fd: io.Base or file object, the stream that the Drive file's
          contents will be written to.
    """
    request = drive_service.files().get_media(fileId=file_id)
    media_request = http.MediaIoBaseDownload(local_fd, request)

    while True:
        try:
            (download_progress, done) = media_request.next_chunk()
        except errors.HttpError as error:
            print('An error occurred: %s' % error)
            return
        if download_progress:
            print('Download Progress: %d%%' % int(download_progress.progress() * 100))
        if done:
            print('Download Complete')
            return

if __name__ == '__main__':
    main()
