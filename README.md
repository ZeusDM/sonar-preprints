# SONAR: Signal Of New Academic Releases

SONAR (Signal Of New Academic Releases) is a Python program designed to notify users about new academic papers published on arXiv.org that match their specified search queries. It automates the process of querying the arXiv API, formatting the results, and sending email notifications to users. This tool is ideal for researchers and academics who want to stay updated on the latest publications in their fields of interest.

We thank arXiv for use of its open access interoperability.

---

## Someone Else Will Run the Code for Me

If someone else is managing the execution of this program for you, you only need to provide a configuration file in YAML format. This file should include your email address, the last time the program was run for you (optional), and your search query. Here's an example of what your configuration file should look like:

### Example User Configuration File (`config.yaml`)

```yaml
user: Your Name
email_address: your_email@example.com
last_run: '2025-05-23 14:41:43'  # Optional, leave blank if running for the first time
search_query: au:"Author Name" OR au:"Author Name2" OR (all:"Keyword" ANDNOT all:"Keyword2")
```

Provide this file to the person running the program, and they will handle the rest.

---

## I Will Run the Code for Me and My Friends

If you plan to run the program for yourself and others, follow these steps:

### 1. Set Up the Configuration File (`config.yaml`)

You need to create a `config.yaml` file to configure the email server settings. This file should include the SMTP server, port, and the sender's email address. Here's an example:

```yaml
SMTP_SERVER: "smtp.example.com"
SMTP_PORT: 25
FROM_ADDRESS: "your_email@example.com"
```

> **⚠ Warning:** Some university systems (like the author's) allow sending emails from within their servers without requiring a password. Make sure the SMTP server and port are correct for your email provider. If your SMTP server requires authentication, you will need to modify the code to include login credentials, and if you do so, you are invited to submit a pull request to the repository.

---

### 2. Prepare User Data Files

For each user, create a YAML file in the `users/` directory. Each file should follow the format shown in the "Someone Else Will Run the Code for Me" section. Ensure the `users/` directory exists and contains all user files.

---

### 3. Install Dependencies

Install the required Python libraries using `pip`:

```sh
pip install requests pyyaml
```

---

### 4. Run the Program

Run the program using the following command:

```sh
python sonar.py --users-dir users/ --config config.yaml
```

This will process all user files in the `users/` directory and send email notifications.

---

### 5. Automate with a Cronjob

To automate the program, set up a cronjob to run it periodically. For example, to run the program every Saturday at 6 AM, add the following line to your crontab:

```sh
0 6 * * 6 /path/to/python /path/to/sonar.py --users-dir /path/to/users/ --config /path/to/config.yaml
```

Replace `/path/to/python`, `/path/to/sonar.py`, `/path/to/users/`, and `/path/to/config.yaml` with the appropriate paths on your system.

---

By following these steps, you can ensure that you and your friends receive regular updates on new academic releases from arXiv.org.