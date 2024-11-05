import streamlit as st
import pandas as pd
import time
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import threading

# variables
trader_dashboards = defaultdict(list)
lock = threading.Lock()
completed_events = 0
total_events = 0
skipped_events_by_trader = defaultdict(list)

# function to set up chromedriver
def setup_chromedriver():
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--headless=old")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920x1080")
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging", "disable-automation"])

    # disabling images to speed up
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")

    driver_path = ChromeDriverManager().install()
    service = Service(driver_path, log_path=None)
    driver = webdriver.Chrome(service=service, options=options)
    return driver_path, options

# function to search and click checkboxes
def search_and_click(driver, search_box, search_term):
    search_box.clear()
    search_box.send_keys(search_term)
    try:
        checkbox = driver.find_element(By.XPATH, "(//input[@type='checkbox'])[2]")  # selects 2nd checkbox
        checkbox.click()
        return True
    except NoSuchElementException:
        return False

# function to handle dashboard actions
def handle_dashboard(driver, dashboard_urls):
    dashboard_button = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, 'dashboard-button')))
    dashboard_url = dashboard_button.get_attribute('href')
    dashboard_urls.append(dashboard_url)
    actions = ActionChains(driver)
    select_all_checkbox = driver.find_element(By.ID, 'checkbox-all-events')
    actions.double_click(select_all_checkbox).perform()  # unselects all events for the next dashboard

# function to create dashboard for each trader
def process_trader(trader, group, username, password):
    global completed_events
    driver_path, options = setup_chromedriver()
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)

    # automate login process & event search
    driver.get('https://blue.soccer.inhouse.paddypower.com/soccer-ui-services/#/search')
    driver.find_element(By.ID, 'userNameInput').send_keys(username)
    driver.find_element(By.ID, 'passwordInput').send_keys(password)
    driver.find_element(By.ID, 'submitButton').click()

    # Wait for the search button to appear and click it
    search_button = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CLASS_NAME, 'search-button'))
    )
    search_button.click()

    dashboard_urls = []
    event_count = 0
    skipped_events = []

    search_box = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'results-search-filter')))

    # Process each event for the trader
    for index, row in group.iterrows():
        event_name = row['Formatted Event']
        home_team = row['Home Team']
        away_team = row['Away Team']

        if pd.isna(away_team):
            skipped_events.append(event_name)
            continue

        # Search for event, home team, or away team, and check its checkbox
        if not search_and_click(driver, search_box, event_name):
            if not search_and_click(driver, search_box, home_team):
                if not search_and_click(driver, search_box, away_team):
                    skipped_events.append(event_name)

        event_count += 1

        # Clear search box after selecting the event
        search_box.clear()

        # Create a new dashboard after 50 events
        if event_count % 50 == 0:
            # Refresh the page after clearing the search box for the last event
            driver.refresh()  # Clear the page for the next batch
            handle_dashboard(driver, dashboard_urls)  # Create a dashboard after every 50 events
            event_count = 0

    # Handle any remaining events that don't make a full batch of 50
    if event_count % 50 != 0:
        # Refresh the page after clearing the search box for the last event
        driver.refresh()
        handle_dashboard(driver, dashboard_urls)

    # Store the dashboard URLs for the trader
    with lock:
        trader_dashboards[trader].extend(dashboard_urls)

    # Store skipped events for each trader
    if skipped_events:
        with lock:
            skipped_events_by_trader[trader].extend(skipped_events)

    driver.quit()

# function to run the automation
def run_automation(df, username, password):
    global total_events, completed_events

    sorted_trader_groups = df.groupby('Assign a trader')

    total_events = len(df)
    trader_event_count = {trader: len(group) for trader, group in sorted_trader_groups}

    # Start the progress and time updates in multithreaded processing
    start_time = time.time()
    progress_bar = st.progress(0)  # Progress bar placeholder
    timer_placeholder = st.empty()  # Timer placeholder for real-time updates

    # function to display progress for streamlit
    def display_progress(completed_events, total_events, elapsed_time):
        progress_percentage = (completed_events / total_events) if total_events > 0 else 0
        progress_bar.progress(progress_percentage)
        timer_placeholder.write(f"Progress: {completed_events}/{total_events} events | Time: {int(elapsed_time)}s")

    # Use ThreadPoolExecutor for multithreading
    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(process_trader, trader, group, username, password): trader for trader, group in sorted_trader_groups}
        for future in as_completed(futures):
            trader = futures[future]
            try:
                future.result()  # ensure the task completed
                trader_events = trader_event_count[trader]
                with lock:
                    completed_events += trader_events
                elapsed_time = time.time() - start_time
                display_progress(completed_events, total_events, elapsed_time)
            except Exception as e:
                st.write(f"Error processing {trader}: {e}")

    st.write("\nAll traders processed.")

    # Group traders back together after splitting in batches
    combined_trader_dashboards = defaultdict(list)
    combined_trader_event_count = defaultdict(int)

    for trader, urls in trader_dashboards.items():
        base_trader_name = ''.join([i for i in trader if not i.isdigit()]).strip()
        combined_trader_dashboards[base_trader_name].extend(urls)
        combined_trader_event_count[base_trader_name] += trader_event_count.get(trader, 0)

    # Sort traders, keeping 'Unassigned' at the end
    sorted_traders = sorted(combined_trader_dashboards.keys(), key=lambda x: (x == "Unassigned", x))

    # Display results for each trader
    for trader in sorted_traders:
        # Add a horizontal line separator before each trader
        st.markdown("---")

        urls = combined_trader_dashboards[trader]
        total_games = combined_trader_event_count.get(trader, 0)  # Get event count for combined traders
        trader_name_bold = f"**{trader}**"

        # Create numbered dashboard links and format them as "Dashboard: 1, 2, 3, ..."
        dashboard_links = [f"[{i+1}]({url})" for i, url in enumerate(urls)]

        # Display the trader's name, total games, and the dashboard links on the same line
        st.markdown(f"{trader_name_bold}: {total_games} games. Dashboard: {', '.join(dashboard_links)}")

        # Handle skipped events
        if skipped_events_by_trader[trader]:
            st.write(f"Skipped events for {trader}:")
            for event in skipped_events_by_trader[trader]:
                st.write(event)
        else:
            st.write(f"Skipped events for {trader}: None")

# streamlit interface
st.title("Trader Dashboard Automation")

# Username and password inputs
username = st.text_input("Enter your username")
password = st.text_input("Enter your password", type="password")

# CSV File Uploader
uploaded_file = st.file_uploader("Drag and drop a CSV file here", type=["csv"])

# Start Creation button
if st.button('Start Creation'):
    if uploaded_file is not None:
        # Start process when button is clicked
        st.write("Creation started. Please wait...")

        # Load the CSV file
        df = pd.read_csv(uploaded_file)

        # Proceed with the rest of the script as before
        df['Date'] = pd.to_datetime(df['Date'], format='%d/%m/%Y %H:%M')  # Convert date & time
        df = df[(df['Date'].dt.time >= pd.to_datetime('04:00').time()) & (df['Date'].dt.time <= pd.to_datetime('22:29').time())]  # Take events between 4am and 10:29pm inclusive
        df = df.dropna(subset=['Event'])  # Drop rows with nothing in Event column
        df = df[df['Scheduled for in-play'] != 'No']
        df = df.sort_values(by=['Scheduled for in-play'], ascending=False).drop_duplicates(subset='Event', keep='first')  # Remove duplicates
        df['Formatted Event'] = df['Event'].apply(lambda x: " |v| ".join([f"|{team.strip()}|" for team in x.split(" v ")]))  # Format for UI search
        df[['Home Team', 'Away Team']] = df['Event'].str.split(' v ', expand=True)  # Columns for home & away teams
        df['Assign a trader'] = df['Assign a trader'].replace('-', 'Unassigned')
        df['Assign a trader'] = df['Assign a trader'].str.replace(r'\d+', '', regex=True).str.replace(r'\(.*\)', '', regex=True).str.strip()
        df = df.sort_values(by=['Assign a trader', 'Date'])

        # Split traders into batches of 50 events
        df['batch_trader'] = df.groupby('Assign a trader').cumcount() // 50 + 1
        df['Assign a trader'] = df.apply(lambda row: f"{row['Assign a trader']}{row['batch_trader']}" if row['batch_trader'] > 1 else row['Assign a trader'], axis=1)
        df = df.drop(columns=['batch_trader'])

        # Run the automation
        run_automation(df, username, password)
    else:
        # Warning if CSV file is not uploaded
        st.warning("Add CSV file first before starting the creation.")