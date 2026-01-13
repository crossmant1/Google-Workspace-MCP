Part I- Google Console

1. Determine the server’s tools. 

2. Go to <https://console.cloud.google.com/> -> APIs and Services.

3. Search for and enable all the APIs you will need, e.g., Gmail API, Calender API. 

4. From the sidebar, click OAuth Concent Screen, then from the new sidebar, click Audience. 

5. Under Test Users, add every email address of accounts you want to access the service (For testing purposes, idk how to make a production app).

6. Next, click Clients from the sidebar.

7. Click ‘Create client’

8. Application type is ‘Web Application’

9. Give it a relevant name and click create. Immediately copy the Client ID and Client Secret. Store them safely. 

10. Go to the server’s environment variables or .env file. The sever should need the Client ID and Client secret to function, so you can add these. 

11. Find the sever’s endpoint URI and add it to ‘Authorized Access URIs.’ Also add the server’s endpoint with the authentication endpoint. E.g. https\://the-service-on-something.com/oauth2callback. Additionally, add <https://flyergpt.udayton.edu>.

Part II- Docker setup

1. Install and login to both Docker Desktop and Docker Hub. 

2. In the command prompt, navigate to the folder with the server code. 

3. Run this command: docker build -t your-image-name . (that period with a space before it is important to copy the whole folder)

4. Create a public repository on Docker and give it a name (your-online-image-name). 

5. Run: docker tag your-image-name your-docker-username/your-online-image-name:latest

6. Run: docker push your-docker-username/your-online-image-name:latest

Branch: 

Either go the Azure-Docker route (paid) or the Render-Docker route (temporary, free). 

\


Azure Branch:

Part III- Azure Resource Group

1. Go to [portal.azure.com](http://portal.azure.com) and login. 

2. Search for ‘Resource group’ and click create. 

3. Enter a relevant name and select a server region nearby, make sure to note the server region.

Part IV-Azure SQL Database

1. In the search bar, type SQL Database, select it, then click ‘Create new.’

2. Select subscription tier, and select the resource group from earlier. 

3. Enter a database name

4. For ‘Server,’ click ‘Create new.’ Enter a name and the same location as the Resource Group.

5. Under ‘Authentication method,’ select ‘Use SQL authentication.’

6. Enter a username and a very strong password. Also make certain you store these credentials safely, then click ok. 

7. Select the computational power and storage required. 

8. Work through the rest of the configuration settings as needed. 

9. After creating the database, navigate to it and in the top bar, select ‘Set server firewall.’

10. At th bottom, under ‘Exceptions,’ select ‘Allow Azure services and resources to access this server.’

11. Back on the main database page, click ‘Query editor’ on the sidebar. 

12. Enter the admin credentials and run any SQL code needed, e.g., creating tables and rows. 

Part V- Azure Container App

1. Ensure a Docker image as been created from the server. 

2. Search ‘Container App’ on Azure and click ‘Create.’

3. Select the plan and the Resource Group from before. 

4. Give it a name and set the ‘Deployment Source’ to be… TBD 

Render Branch:

Part III- Inline storage

1. Reconfigure the code to store all data active memory in arrays. 

2. Note that all data is lost on a server restart and this is a temporary method. 

Part IV- Render Configuration

1. Visit [render.com](http://render.com) and login. 

2. On the dashboard, add a new “Web Service”

3. Toggle over to “Existing Image”

4. Enter your Docker image URL with the following syntax: [docker.io/your-docker-username/your-online-image-name:latest](http://docker.io/your-docker-username/your-online-image-name:latest)

5. Select the free plan. 

6. Add all the needed environment variables, including the Google Client Secret & ID and redirect URI. This must be registered in the Google Cloud Console. It is not available until the instance is deployed. Deploy it and look near the top of the page, under the little docker image there is a purple link. Copy this link and add /oauth2callback to the end and add it to the redirect URI environment variable and as a valid link in the Google Cloud Console. 

7. Save and redeploy the instance. 

\


End of Branch

The branches merge

Final Part- FlyerGPT Configuration

1. Login to FlyerGPT and navigate to the “Connections” tab. 

2. Create a new connection with HTTPS. 

3. Give it a name and description. 

4. Paste the Render or Azure primary URL with the correct path, e.g. /mcp. 

5. Click Save. 

6. Navigate to the “Agents” tab and select an existing one or create a new one. 

7. Select the correct connection from the dropdown list. 
