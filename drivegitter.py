import httplib2
import os
import sys
import threading

from pathlib import Path
from queue import Queue

from apiclient import discovery
from apiclient import errors
from apiclient import http
from oauth2client import client
from oauth2client import tools
from oauth2client.file import Storage

from git import Repo

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
    credential_path = os.path.join(credential_dir,
                                   'drivegitter.json')

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
    """Shows basic usage of the Google Drive API.

    Creates a Google Drive API service object and outputs the names and IDs
    for up to 10 files.
    """
    credentials = get_credentials()
    http = credentials.authorize(httplib2.Http())
    global drive_service
    drive_service = discovery.build('drive', 'v2', http=http)

    output_dir = Path(flags.output_directory)
    output_dir.mkdir(exist_ok=True)

    #repo = Repo(output_dir.as_posix())

    process_folder(flags.root_file_id, output_dir)


class DriveFile:
    def __init__(self, file_id, parent_path):
        self.file_id = file_id
        self.parent_path = parent_path

class ProcessFileThread(threading.Thread):
    def __init__(self, file_queue):
        threading.Thread.__init__(self)
        self.file_queue = file_queue

    def run(self):
        while True:
            drive_file = self.file_queue.get()
            self.processFile(drive_file)
            self.file_queue.task_done()

    def processFile(self, drive_file):
        file = drive_service.files().get(fileId = drive_file.file_id).execute()

        filename = file['title'].strip()
        file_path = Path(drive_file.parent_path, filename)

        f = open(file_path.as_posix(), 'wb')
        download_file(drive_file.file_id, f)
        f.close()

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
    
    if file['mimeType'] == 'application/vnd.google-apps.folder':
        
        file_path.mkdir(exist_ok = True)
        process_folder(file_id, file_path)
    else:
        f = open(file_path.as_posix(), 'wb')
        download_file(file_id, f)
        f.close()

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
