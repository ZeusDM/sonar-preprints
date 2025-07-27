import requests
from datetime import datetime, timedelta
from xml.etree import ElementTree
import os
import yaml
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import argparse
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sonar.log"),
        logging.StreamHandler()
    ]
)

def load_config(config_file):
    """
    Load configuration from a YAML file.
    """
    try:
        with open(config_file, "r") as file:
            config = yaml.safe_load(file)
        return config
    except FileNotFoundError:
        logging.error(f"Configuration file not found: {config_file}")
        raise
    except yaml.YAMLError as e:
        logging.error(f"Error parsing YAML configuration file: {e}")
        raise

def compute_weekly_range(last_run_datetime_str=None):
    """
    Computes the datetime range from the last run datetime to now.
    If no last run datetime is provided or is invalid, it defaults to the
    range from 7 days ago to now.
    """
    now = datetime.now()
    if last_run_datetime_str:
        try:
            last_run_datetime = datetime.strptime(last_run_datetime_str, "%Y-%m-%d %H:%M:%S")
            start_datetime = last_run_datetime + timedelta(seconds=1)
            end_datetime = now
            return start_datetime.strftime("%Y-%m-%d %H:%M:%S"), end_datetime.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            logging.warning("Invalid last run datetime format. Falling back to the last 7 days.")
            start_datetime = now - timedelta(days=7)
            end_datetime = now
            return start_datetime.strftime("%Y-%m-%d %H:%M:%S"), end_datetime.strftime("%Y-%m-%d %H:%M:%S")
    else:
        start_datetime = now - timedelta(days=7)
        end_datetime = now
        return start_datetime.strftime("%Y-%m-%d %H:%M:%S"), end_datetime.strftime("%Y-%m-%d %H:%M:%S")

def search_arxiv_api(search_query, start_datetime, end_datetime, max_results=100):
    """
    Query the arXiv API and return parsed results.
    """
    # Clean up the search query by replacing newlines with spaces
    search_query = search_query.replace('\n', ' ')
    
    base_url = "http://export.arxiv.org/api/query?"
    from_datetime = start_datetime.replace("-", "").replace(" ", "").replace(":", "")
    to_datetime = end_datetime.replace("-", "").replace(" ", "").replace(":", "")
    query = f"search_query=({search_query})+AND+submittedDate:[{from_datetime}+TO+{to_datetime}]"
    url = f"{base_url}{query}&start=0&max_results={max_results}"

    for attempt in range(3):  # Try up to 3 times
        try:
            time.sleep(3)  # Sleep for 3 seconds before making the request
            response = requests.get(url)
            if response.status_code == 200:
                break  # Exit the loop if the request is successful
            else:
                logging.warning(f"Attempt {attempt + 1}: API request failed with status code {response.status_code}")
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1}: Error fetching results from arXiv API: {e}")
        if attempt == 2:  # If this is the last attempt, raise the exception
            logging.error("All attempts to fetch results from arXiv API failed.")
            raise Exception("Failed to fetch results from arXiv API after 3 attempts")

    logging.debug(f"Query URL: {url}")

    data = response.content
    root = ElementTree.fromstring(data)
    results = []

    # Parse the XML response
    root = ElementTree.fromstring(data)
    results = []
    for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
        title = entry.find("{http://www.w3.org/2005/Atom}title").text
        link = entry.find("{http://www.w3.org/2005/Atom}id").text
        authors = [author.find("{http://www.w3.org/2005/Atom}name").text for author in entry.findall("{http://www.w3.org/2005/Atom}author")]
        updated = datetime.strptime(entry.find("{http://www.w3.org/2005/Atom}updated").text, "%Y-%m-%dT%H:%M:%SZ" )
        published = datetime.strptime(entry.find("{http://www.w3.org/2005/Atom}published").text, "%Y-%m-%dT%H:%M:%SZ")
        summary = entry.find("{http://www.w3.org/2005/Atom}summary").text
        comment = entry.find("{http://arxiv.org/schemas/atom}comment")
        comment_text = comment.text if comment is not None else ""
        primary_category = entry.find("{http://arxiv.org/schemas/atom}primary_category").attrib.get("term", None)
        categories = [category.attrib.get("term", None) for category in entry.findall("{http://arxiv.org/schemas/atom}category")]
        if primary_category not in categories:
            categories = [primary_category] + categories

        results.append({
            "title": title,
            "link": link,
            "authors": authors,
            "updated": updated,
            "published": published,
            "summary": summary,
            "comment": comment_text,
            "primary_category": primary_category,
            "categories": categories
        })

    results.sort(key=lambda x: x["published"], reverse=True)
    return results

def process_user_data(user_data, args):
    user_name = user_data["user"]
    email_address = user_data["email_address"]
    search_query = user_data["search_query"]

    logging.info(f"Processing user: {user_name}")

    # Compute datetime range based on per-user last run
    last_run = user_data.get("last_run", None)
    date_from, date_to = compute_weekly_range(last_run)
    logging.info(f"Date range: {date_from} to {date_to}")

    # Perform ArXiv API search
    try:
        search_results = search_arxiv_api(search_query, date_from, date_to)
        logging.info(f"Found {len(search_results)} results for {user_name}")
    except Exception as e:
        logging.error(f"Error fetching arXiv results for {user_name}: {e}")
        return

    # Format search results for email
    results_html = ""
    if search_results:
        for result in search_results:
            results_html += f"<p><strong>Title:</strong> <a href=\"{result['link']}\">{result['title']}</a><br>\n"
            results_html += f"<strong>Authors:</strong> {', '.join(result['authors'])}<br>\n"
            results_html += f"{result['published'].strftime('%Y-%m-%d %H:%M:%S')}<br>\n"
            results_html += f"<i>Summary:</i> {result['summary']}</p>\n"
            results_html += "<hr>\n"
    else:
        logging.warning(f"No results found for user '{user_name}'")
        results_html = "<p>No new articles found based on your search query since the last run.</p>"

    # Compose email
    subject = f"Your Weekly SONAR ({date_from} to {date_to}, {user_name})"
    body = f"""<html>
<head></head>
<body>
    <p>Hello {user_name},</p>
    <p>Here are the arXiv updates since the last time this program was run ({date_from} to {date_to}):</p>
    {results_html}
    <p>Your search query was: <i>{search_query}</i></p>
    <p>We thank arXiv for use of its open access interoperability.</p>
    <p>Best regards, SONAR</p>
</body>
</html>"""
    msg = MIMEMultipart()
    msg["From"] = FROM_ADDRESS
    msg["To"] = email_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))

    logging.info(f"Sending email to {email_address}")

    # Adjust flags based on test mode
    if args.test:
        args.print_only = True
        args.no_update = True

    # Send email or print based on print-only mode
    if args.print_only:
        logging.info(f"Print-Only Mode enabled — email to {email_address} would have been sent.")
        print(f"Print-Only Mode: Email to {email_address}:\nSubject: {subject}\nBody:\n{body}\n")
        email_sent = True
    else:
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.sendmail(FROM_ADDRESS, email_address, msg.as_string())
                logging.info(f"Email sent to {email_address}")
                email_sent = True
        except Exception as e:
            logging.error(f"Failed to send email to {email_address}: {e}")
            email_sent = False

    # Update the user's YAML with the new last run timestamp only if email was sent or printed
    if email_sent and not args.no_update:
        user_data["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(user_data["filepath"], "w") as file:
            update_data = user_data.copy()
            update_data.pop("filepath", None)  # Remove filepath from the data to be saved
            yaml.safe_dump(update_data, file, width=999999)
        logging.info(f"Updated last run timestamp for user: {user_name}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArXiv Filter Script")
    parser.add_argument("--config", default="config.yaml", help="Path to the configuration YAML file.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--users-dir", help="Path to the directory containing individual user YAML files.")
    group.add_argument("--user-file", help="Path to a single YAML file containing user data.")
    parser.add_argument("--test", action="store_true", help="Enable test mode (implies --print-only and --no-update).")
    parser.add_argument("--print-only", action="store_true", help="Print emails instead of sending them.")
    parser.add_argument("--no-update", action="store_true", help="Do not update the last run timestamp in the user YAML file.")
    parser.add_argument("--log-level", default="INFO", help="Set logging level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level.upper(), logging.INFO))

    # Load configuration
    config = load_config(args.config)
    SMTP_SERVER = config.get("SMTP_SERVER", "localhost")
    SMTP_PORT = config.get("SMTP_PORT", 25)
    FROM_ADDRESS = config.get("FROM_ADDRESS", "example@example.com")

    if args.users_dir:
        users_dir = args.users_dir
        if not os.path.isdir(users_dir):
            logging.error(f"Directory not found: {users_dir}")
        else:
            for filename in os.listdir(users_dir):
                if filename.endswith(".yaml"):
                    filepath = os.path.join(users_dir, filename)
                    try:
                        with open(filepath, "r") as file:
                            user_data = yaml.safe_load(file)
                            user_data['filepath'] = filepath 
                            process_user_data(user_data, args)
                    except FileNotFoundError:
                        logging.error(f"User data file not found: {filepath}")
                    except yaml.YAMLError as e:
                        logging.error(f"Error parsing YAML in {filepath}: {e}")
    elif args.user_file:
        user_file = args.user_file
        try:
            with open(user_file, "r") as file:
                user_data = yaml.safe_load(file)
                process_user_data(user_data, args)
        except FileNotFoundError:
            logging.error(f"User data file not found: {user_file}")
        except yaml.YAMLError as e:
            logging.error(f"Error parsing YAML in {user_file}: {e}")