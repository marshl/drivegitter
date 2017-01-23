import httplib2
import os
import sys
import threading
import git
from inspect import getmembers
from pprint import pprint

from pathlib import Path
from queue import Queue
from subprocess import call

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

file_queue = Queue()
drive_service = None

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
        else: # Needed only for compatibility with Python 2.6
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

    os.chdir(flags.output_directory)
    repo = git.Repo.init(flags.output_directory)

    process_folder(flags.root_file_id, output_dir)

def process_folder(folder_id, folder_path):

    root_file = drive_service.files().get(fileId = folder_id).execute()
    childpage = drive_service.children().list(folderId=folder_id).execute()

    for child in childpage['items']:
        print("File {0}".format(child['id']))

        process_file(child['id'], folder_path)


def process_file(file_id, parent_path):

    file = drive_service.files().get(fileId = file_id).execute()

    #Google drive allows filenames that end with a space, which must be trimmed
    filename = file['title'].strip()
    file_path = Path(parent_path, filename)
    print(filename.encode('utf-8'))

    if file['mimeType'] == 'application/vnd.google-apps.folder':
        file_path.mkdir(exist_ok = True)
        process_folder(file_id, file_path)
    elif not os.path.isfile(file_path.as_posix()):

        file_owner = file['owners'][0]

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
            file_content = drive_service.files().export_media(fileId=file_id, mimeType=output_mt).execute()
            f.write(file_content)
            f.close()

            result = call(['git', 'add', '-f', file_path.as_posix()])
            if result != 0:
                sys.exit('An error occurred during the add')

            user = file['lastModifyingUser']
            message = 'Added {0}'.format(filename)
            result = commit_file(file_path, message, file['modifiedDate'], user, file_owner)
            if result != 0:
                sys.exit('An error occurred during the commit')
        else:
            process_file_revisions(file, parent_path, file_owner)

        if 'trashed' in file['labels'] and file['labels']['trashed'] == True:
            result = call(['git', 'rm', file_path.as_posix()])
            if result != 0:
                sys.exit('An error occurred during the add')
            user = file['lastModifyingUser']
            message = 'Removed {0}'.format(filename)
            result = commit_file(file_path, message, file['modifiedDate'], user, file_owner)
            if result != 0:
                sys.exit('An error occurred during the commit')

def process_file_revisions(drive_file, parent_path, file_owner):

    filename = drive_file['title'].strip()
    file_path = Path(parent_path, filename)

    revisions = drive_service.revisions().list(fileId=drive_file['id']).execute()

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

        result = call(['git', 'add', '-f', file_path.as_posix()])
        if result != 0:
            sys.exit('An error occurred during the add')

        user = revision['lastModifyingUser']
        message = 'Added file {0}'.format(filename) if revision_number == 1 else 'Modified {0} (revision {1}'.format(filename, revision_number)
        result = commit_file(file_path, message, revision['modifiedDate'], user, file_owner)
        if result != 0:
            sys.exit('An error occurred during the commit')

def commit_file(file_path, message, date, modified_by_user, file_owner):

    email = modified_by_user['emailAddress'] if 'emailAddress' in modified_by_user else file_owner['emailAddress']
    return call(['git', 'commit',
          '--message', message,
          '--author="{0}" <{1}>'.format(modified_by_user['displayName'], email),
          '--date=' + date,
          '--allow-empty',
          file_path.as_posix()])


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
