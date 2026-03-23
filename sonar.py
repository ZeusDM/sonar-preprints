import requests
import urllib.parse
import re
from datetime import datetime, timedelta
from xml.etree import ElementTree
from pathlib import Path
import yaml
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import argparse
import logging
import time
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sonar.log"),
        logging.StreamHandler()
    ]
)


class ArxivAPIError(Exception):
    """Raised when the arXiv API fails repeatedly and the program should stop."""
    pass

def load_config(config_file):
    """
    Load configuration from a YAML file.
    """
    try:
        config = yaml.safe_load(Path(config_file).read_text())
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

def make_queries_from_categories_and_keywords(categories, keywords, max_len=2000):
    """
    Given lists of categories and keywords, construct one or more search_query strings
    that combine the categories and keywords, ensuring that the encoded length of each
    query does not exceed max_len.
    Returns a list of search_query strings (WITHOUT the submittedDate clause).
    """
    if not keywords:
        if categories:
            categories_expr = " OR ".join([f"cat:{c}" for c in categories if c])
            return [categories_expr]
        return []

    # Prepare category expression
    categories = [c for c in (categories or []) if c]
    categories_expr = " OR ".join([f"cat:{c}" for c in categories]) if categories else ""

    queries = []
    chunk = []

    for token in keywords:
        tentative = chunk + [token]
        kw_clause = " OR ".join(tentative)
        if categories_expr:
            candidate = f"({categories_expr}) AND ({kw_clause})"
        else:
            candidate = f"({kw_clause})"

        # Measure only the encoded search_query part (not base URL or date)
        # as requested — encode the candidate itself and enforce max_len on that.
        encoded_query = urllib.parse.quote_plus(candidate)

        if len(encoded_query) > max_len:
            if not chunk:
                raise ValueError(f"Single keyword too long to construct a safe query: {token}")
            kw_clause_prev = " OR ".join(chunk)
            if categories_expr:
                q_prev = f"({categories_expr}) AND ({kw_clause_prev})"
            else:
                q_prev = f"({kw_clause_prev})"
            queries.append(q_prev)
            chunk = [token]
        else:
            chunk = tentative

    if chunk:
        kw_clause = " OR ".join(chunk)
        if categories_expr:
            q = f"({categories_expr}) AND ({kw_clause})"
        else:
            q = f"({kw_clause})"
        queries.append(q)

    return queries

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
        if attempt == 2:  # If this is the last attempt, raise a fatal API error
            logging.error("All attempts to fetch results from arXiv API failed.")
            # Raise a specific exception so callers can decide to stop the whole program
            raise ArxivAPIError("Failed to fetch results from arXiv API after 3 attempts")

    logging.debug(f"Query URL: {url}")

    data = response.content
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

def normalize_input(user_data):
    """
    Normalize user data, ensuring ``keywords`` and ``categories`` are always
    lists, regardless of how they appear in the YAML (a plain string, a
    comma/newline-separated string, or already a list).

    Returns a tuple ``(keywords, categories)`` of lists.
    """
    raw_keywords = user_data.get("keywords", [])
    if isinstance(raw_keywords, str):
        keywords = [k.strip() for k in re.split(r"[,\n]", raw_keywords) if k.strip()]
    elif isinstance(raw_keywords, list):
        keywords = [k for k in raw_keywords if k]
    else:
        keywords = []

    raw_cats = user_data.get("categories", [])
    if isinstance(raw_cats, str):
        categories = [c.strip() for c in re.split(r"[,\n]", raw_cats) if c.strip()]
    elif isinstance(raw_cats, list):
        categories = [c for c in raw_cats if c]
    else:
        categories = []

    return keywords, categories


def build_email_body(user_name, date_from, date_to, search_results, categories_list, keywords_list):
    """
    Build the HTML email subject and body from search results.

    Returns a tuple ``(subject, body)`` where both are strings.
    """
    results_html = ""
    if search_results:
        for result in search_results:
            results_html += f"<p><strong>Title:</strong> <a href=\"{result['link']}\">{result['title']}</a><br>\n"
            results_html += f"<strong>Authors:</strong> {', '.join(result.get('authors', []))}<br>\n"
            published = result.get("published")
            if published:
                results_html += f"{published.strftime('%Y-%m-%d %H:%M:%S')}<br>\n"
            results_html += f"<i>Summary:</i> {result.get('summary', '')}</p>\n"
            results_html += "<hr>\n"
    else:
        results_html = "<p>No new articles found based on your search query since the last run.</p>"

    categories_display = ', '.join(categories_list) if categories_list else "(none)"
    keywords_display = ', '.join(keywords_list) if keywords_list else "(none)"

    subject = f"Your Weekly SONAR ({date_from[:10]} to {date_to[:10]}, {user_name})"
    body = f"""<html>
<head></head>
<body>
    <p>Hello {user_name},</p>
    <p>Here are the arXiv updates since the last time this program was run ({date_from} to {date_to}):</p>
    {results_html}
    <p>Your categories: <i>{categories_display}</i></p>
    <p>Your keywords: <i>{keywords_display}</i></p>
    <p>We thank arXiv for use of its open access interoperability.</p>
    <p>Best regards, SONAR</p>
</body>
</html>"""
    return subject, body


def update_last_run(user_data):
    """
    Update the ``last_run`` timestamp in the user's YAML file.
    """
    user_data["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_data = user_data.copy()
    update_data.pop("filepath", None)  # Remove filepath from the data to be saved
    Path(user_data["filepath"]).write_text(yaml.safe_dump(update_data, width=999999))
    logging.info(f"Updated last run timestamp for user: {user_data['user']}")


def process_user_data(user_data, args):
    user_name = user_data["user"]
    email_address = user_data["email_address"]
    logging.info(f"Processing user: {user_name}")

    # Stage 1: Normalize input — ensure keywords and categories are always lists
    keywords_list, categories_list = normalize_input(user_data)

    # Build queries based on available input fields
    queries = []
    if "keywords" in user_data and user_data["keywords"]:
        if not keywords_list and not categories_list:
            logging.warning(f"No keywords or categories specified for user: {user_name}")
            return

        try:
            max_len = globals().get("MAX_QUERY_URL_LEN", 3500)
            queries = make_queries_from_categories_and_keywords(categories_list, keywords_list, max_len=max_len)
        except Exception as e:
            logging.error(f"Error building queries for {user_name} from keywords/categories: {e}")
            return
    elif "search_queries" in user_data and isinstance(user_data["search_queries"], list):
        # Filter out empty / null entries and ensure they're strings
        queries = [q for q in user_data["search_queries"] if q]
        # treat these as keywords for display; categories not used in legacy mode
        keywords_list = queries.copy()
        categories_list = []
    elif "search_query" in user_data and user_data["search_query"]:
        queries = [user_data["search_query"]]
        # treat the single query as keyword for display; categories not used in legacy mode
        keywords_list = queries.copy()
        categories_list = []
    else:
        logging.warning(f"No search_query/search_queries or keywords specified for user: {user_name}")
        return

    # Compute datetime range based on per-user last run
    last_run = user_data.get("last_run", None)
    date_from, date_to = compute_weekly_range(last_run)
    logging.info(f"Date range: {date_from} to {date_to}")

    # Perform ArXiv API searches for each query and merge results uniquely by link
    merged = {}  # key: link, value: result dict
    total_found = 0
    for q in queries:
        try:
            results = search_arxiv_api(q, date_from, date_to)
            logging.info(f"Found {len(results)} results for {user_name} (query: {q})")
            total_found += len(results)
        except Exception as e:
            # If the arXiv API failed repeatedly, it's a fatal condition: stop the whole program.
            if isinstance(e, ArxivAPIError):
                logging.error(f"Fatal arXiv API error for {user_name} (query: {q}): {e}")
                logging.error("Halting execution due to repeated arXiv API failures.")
                # Exit immediately with non-zero status to indicate failure
                sys.exit(1)
            logging.error(f"Error fetching arXiv results for {user_name} (query: {q}): {e}")
            continue

        for r in results:
            link = r.get("link")
            if not link:
                continue
            # Keep the entry with the most recent published date if duplicate
            existing = merged.get(link)
            if not existing:
                merged[link] = r
            else:
                try:
                    if r.get("published") and existing.get("published") and r["published"] > existing["published"]:
                        merged[link] = r
                except Exception:
                    # If published comparison fails, keep existing
                    pass

    search_results = sorted(merged.values(), key=lambda x: x.get("published", datetime.min), reverse=True)

    logging.info(f"Total (raw) results across queries for {user_name}: {total_found}. After dedupe: {len(search_results)}")

    if not search_results:
        logging.warning(f"No results found for user '{user_name}'")

    # Stage 2: Build the email body using the HTML generator
    subject, body = build_email_body(user_name, date_from, date_to, search_results, categories_list, keywords_list)

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

    # Stage 3: Update state — persist the last_run timestamp
    if email_sent and not args.no_update:
        update_last_run(user_data)

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
        users_dir = Path(args.users_dir)
        if not users_dir.is_dir():
            logging.error(f"Directory not found: {users_dir}")
        else:
            for filepath in users_dir.glob("*.yaml"):
                try:
                    user_data = yaml.safe_load(filepath.read_text())
                    user_data['filepath'] = filepath
                    process_user_data(user_data, args)
                except FileNotFoundError:
                    logging.error(f"User data file not found: {filepath}")
                except yaml.YAMLError as e:
                    logging.error(f"Error parsing YAML in {filepath}: {e}")
    elif args.user_file:
        user_file = Path(args.user_file)
        try:
            user_data = yaml.safe_load(user_file.read_text())
            user_data['filepath'] = user_file
            process_user_data(user_data, args)
        except FileNotFoundError:
            logging.error(f"User data file not found: {user_file}")
        except yaml.YAMLError as e:
            logging.error(f"Error parsing YAML in {user_file}: {e}")