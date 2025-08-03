import os
import pickle
import glob
import csv
import time

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- Configuration ---
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
CLIENT_SECRETS_FILE = 'client_secrets.json'

# Define the directory where your video files are located
VIDEOS_DIRECTORY = 'videos'
ALLOWED_VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.webm')

# Define the path to your metadata CSV file
METADATA_CSV_FILE = 'metadata.csv'
# --- End Configuration ---


def get_authenticated_service(account_identifier):
    """
    Handles the OAuth 2.0 flow to authenticate with a specific Google Account.
    It saves and loads user credentials to/from 'token_<account_identifier>.pickle'
    for future use, avoiding repeated browser authentication for the same account.

    Args:
        account_identifier (str): A unique identifier for the Google Account
                                  (e.g., its primary email address).

    Returns:
        googleapiclient.discovery.Resource: A service object for the YouTube Data API (v3),
                                            authenticated for the specified account, or None on failure.
    """
    token_filename = f'token_{account_identifier}.pickle'
    credentials = None

    if os.path.exists(token_filename):
        print(f"Loading credentials for '{account_identifier}' from '{token_filename}'...")
        try:
            with open(token_filename, 'rb') as token:
                credentials = pickle.load(token)
        except Exception as e:
            print(f"Error loading token for '{account_identifier}': {e}. Will re-authenticate.")
            os.remove(token_filename) # Delete corrupted token
            credentials = None

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            print(f"Refreshing credentials for '{account_identifier}'...")
            try:
                credentials.refresh(Request())
            except Exception as e:
                print(f"Error refreshing token for '{account_identifier}': {e}. Will re-authenticate.")
                os.remove(token_filename) # Delete invalid token
                credentials = None
        
        if not credentials or not credentials.valid: # Check again after refresh attempt
            print(f"Authenticating for account '{account_identifier}'... Please open your browser to complete the OAuth flow.")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
                credentials = flow.run_local_server(port=0)
            except Exception as e:
                print(f"Failed to complete authentication for '{account_identifier}': {e}")
                return None # Return None if authentication fails

        if credentials: # Only save if we successfully got credentials
            with open(token_filename, 'wb') as token:
                pickle.dump(credentials, token)
            print(f"Credentials for '{account_identifier}' saved to '{token_filename}'.")
        else:
            return None # Authentication ultimately failed

    return build('youtube', 'v3', credentials=credentials)


def upload_single_video(youtube_service, file_path, title, description, tags, privacy_status, made_for_kids_flag):
    """
    Uploads a single video file to YouTube using the provided authenticated service.

    Args:
        youtube_service: The authenticated YouTube Data API service object for the target account.
        file_path (str): The path to the video file to upload.
        title (str): The title for the YouTube video.
        description (str): The description for the YouTube video.
        tags (list): A list of tags for the YouTube video.
        privacy_status (str): The privacy status of the video ('public', 'private', 'unlisted').
        made_for_kids_flag (bool): True if the video is made for kids, False otherwise.
    Returns:
        dict: The response from the YouTube API for the uploaded video, or None if error.
    """
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': '22'  # Category ID for 'People & Blogs'.
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': made_for_kids_flag
        },
        'localized': { # Optional: for default language of the video's title and description
            'title': title,
            'description': description
        }
    }

    media_body = MediaFileUpload(file_path, mimetype='video/*', chunksize=-1, resumable=True)

    try:
        print(f"Uploading '{os.path.basename(file_path)}' with title: '{title}'...")
        
        request = youtube_service.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media_body
        )

        response = request.execute()

        print(f"Successfully uploaded video! Video ID: {response['id']}")
        print(f"Watch it here: https://www.youtube.com/watch?v={response['id']}") # Correct YouTube watch URL format
        return response

    except Exception as e:
        print(f"An error occurred during upload for '{os.path.basename(file_path)}': {e}")
        return None

def read_metadata_csv(filepath):
    """
    Reads the metadata CSV into a list of dictionaries.
    Returns the fieldnames (header) and the list of video metadata.
    """
    if not os.path.exists(filepath):
        print(f"Error: Metadata CSV file '{filepath}' not found.")
        return None, []

    videos_metadata = []
    fieldnames = []
    with open(filepath, mode='r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        fieldnames = reader.fieldnames

        required_cols = ['filename', 'title', 'description', 'tags', 'privacy_status', 'made_for_kids_flag', 'upload_status', 'uploader_account_email']
        
        missing_cols = [col for col in required_cols if col not in fieldnames]
        if missing_cols:
            print(f"Error: Missing required columns in '{filepath}': {', '.join(missing_cols)}")
            print("Please ensure your CSV header includes all required columns.")
            print(f"Expected header: {','.join(required_cols)}")
            return None, []
        
        for row in reader:
            # Ensure all fieldnames from the header are present as keys in each row's dictionary,
            # even if their value is empty. This prevents DictWriter errors.
            cleaned_row = {field: row.get(field, '') for field in fieldnames}
            videos_metadata.append(cleaned_row)
    return fieldnames, videos_metadata

def write_metadata_csv(filepath, fieldnames, data):
    """Writes a list of dictionaries back to the metadata CSV."""
    with open(filepath, mode='w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"Updated '{filepath}' with latest upload statuses.")


# --- Main execution block ---
if __name__ == '__main__':
    print("Attempting to get authenticated YouTube service...")
    
    # We will get the service inside the loop for the specific account
    youtube_service = None 
    current_uploader_email = None # To track which email is currently authenticated

    try:
        if not os.path.exists(VIDEOS_DIRECTORY):
            print(f"Error: Video directory '{VIDEOS_DIRECTORY}' not found.")
            print(f"Please create a folder named '{VIDEOS_DIRECTORY}' and place your videos inside it.")
            exit()

        csv_fieldnames, videos_to_process = read_metadata_csv(METADATA_CSV_FILE)
        
        if csv_fieldnames is None:
            exit()

        if not videos_to_process:
            print(f"No video entries found in '{METADATA_CSV_FILE}'. Please populate the file.")
            exit()

        print(f"Found {len(videos_to_process)} video entries in '{METADATA_CSV_FILE}'.")

        video_uploaded_this_run = False
        for i, video_data in enumerate(videos_to_process):
            if video_data.get('upload_status', '').lower() == 'pending':
                print(f"\n--- Attempting to upload next pending video: {video_data.get('filename', 'N/A')} ---")
                
                uploader_email = video_data.get('uploader_account_email', '').strip()
                if not uploader_email:
                    print(f"Skipping video '{video_data.get('filename', 'N/A')}' due to missing 'uploader_account_email'. Status set to 'skipped_no_email'.")
                    videos_to_process[i]['upload_status'] = 'skipped_no_email'
                    video_uploaded_this_run = True
                    break # Stop after processing one
                
                # Check if we need to authenticate a different account
                if uploader_email != current_uploader_email:
                    print(f"Switching authentication to: {uploader_email}")
                    youtube_service = get_authenticated_service(uploader_email)
                    if youtube_service:
                        current_uploader_email = uploader_email
                        print(f"Successfully authenticated for {current_uploader_email}!")
                    else:
                        print(f"Could not authenticate for account '{uploader_email}'. Skipping video and further uploads for this run.")
                        videos_to_process[i]['upload_status'] = 'skipped_auth_fail'
                        video_uploaded_this_run = True
                        break # Stop if authentication for this account fails

                if not youtube_service: # If authentication failed for any reason
                    break # Exit the loop, no more uploads for this run

                filename = video_data.get('filename')
                title = video_data.get('title')
                description = video_data.get('description')
                tags_str = video_data.get('tags')
                privacy_status = video_data.get('privacy_status', 'private')
                made_for_kids_flag_str = video_data.get('made_for_kids_flag', 'False')

                if not all([filename, title, description, tags_str]):
                    print(f"Skipping video '{filename}' due to missing essential metadata (title, description, or tags). Status set to 'skipped_missing_meta'.")
                    videos_to_process[i]['upload_status'] = 'skipped_missing_meta'
                    video_uploaded_this_run = True 
                    break 
                
                tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
                made_for_kids_flag = made_for_kids_flag_str.lower() == 'true'

                video_file_path = os.path.join(VIDEOS_DIRECTORY, filename)

                if not os.path.exists(video_file_path):
                    print(f"Warning: Video file '{filename}' listed in CSV not found at '{video_file_path}'. Status set to 'file_not_found'.")
                    videos_to_process[i]['upload_status'] = 'file_not_found'
                    video_uploaded_this_run = True
                    break

                if not video_file_path.lower().endswith(ALLOWED_VIDEO_EXTENSIONS):
                    print(f"Warning: Video file '{filename}' has an unsupported extension. Status set to 'unsupported_extension'.")
                    videos_to_process[i]['upload_status'] = 'unsupported_extension'
                    video_uploaded_this_run = True
                    break

                upload_response = upload_single_video(
                    youtube_service, # Use the specific service obtained for this email
                    video_file_path,
                    title,
                    description,
                    tags,
                    privacy_status,
                    made_for_kids_flag
                )

                if upload_response:
                    videos_to_process[i]['upload_status'] = 'uploaded'
                    print(f"Video '{filename}' successfully uploaded and status updated to 'uploaded'.")
                else:
                    videos_to_process[i]['upload_status'] = 'failed'
                    print(f"Video '{filename}' failed to upload. Status updated to 'failed'.")
                
                video_uploaded_this_run = True
                break

        if not video_uploaded_this_run:
            print("No new 'pending' videos found in metadata.csv to upload at this time.")
        
        write_metadata_csv(METADATA_CSV_FILE, csv_fieldnames, videos_to_process)


    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        print("Please ensure 'client_secrets.json' is correct and in the same directory.")
        print("Also, check your internet connection and Google Cloud project settings.")